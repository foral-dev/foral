"""`foral serve` — o CONTRATO como MCP server (29/08/2026).

Cada capacidade de LEITURA do contrato vira uma tool MCP com o mesmo nome; um agente
(Claude Code, ou qualquer cliente MCP) enxerga o sistema como tools tipadas, sem nunca
saber que não havia API pública.

Governança embutida (MCP NÃO é porta de contorno):
· capacidade de ESCRITA nasce FORA do server (read-only por default); mesmo listada, a
  execução recusa e devolve a frase de confirmação — o HITL é outra porta;
· `network_json` executa (GET com a sessão salva, sem browser); `playwright_read` é
  anunciada mas responde "modo DOM ainda não servido por aqui" (fail-closed, não 200 oco);
· o host vem do CONTRATO, nunca do chamador.

Transporte: JSON-RPC 2.0 sobre stdio, implementado à mão (auditável, sem dependência
nova). A LÓGICA (`tools_do_contrato`, `tratar_requisicao`) é pura e testável; o laço de
stdio é um wrapper fino.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from foral.contract import Contrato, Escopo, ModoLeitura

PROTO_PADRAO = "2024-11-05"


def _tool_de_capacidade(cap) -> Optional[dict]:
    """Uma tool MCP a partir de UMA capacidade de leitura. Escrita → None (não vira tool)."""
    if cap.escopo == Escopo.ESCRITA:
        return None
    props, req = {}, []
    if cap.lista and cap.cursor and cap.cursor != "completa":
        props[cap.cursor] = {"type": "string",
                             "description": f"cursor de paginação ({cap.cursor})"}
    campos = ", ".join(e.campo for e in cap.extratores) or "—"
    return {
        "name": cap.nome,
        "description": (f"Lê '{cap.nome}' do sistema. Devolve linhas tipadas "
                        f"({campos}). Determinístico, sem browser." if cap.modo == ModoLeitura.NETWORK_JSON
                        else f"Lê '{cap.nome}' (modo DOM). Campos: {campos}."),
        "inputSchema": {"type": "object", "properties": props, "required": req},
    }


def tools_do_contrato(contrato: Contrato) -> list[dict]:
    """Todas as tools servíveis do contrato (só leituras)."""
    return [t for t in (_tool_de_capacidade(c) for c in contrato.capacidades) if t]


def _executar_tool(contrato: Contrato, tenant_id: str, nome: str, args: dict) -> dict:
    """Executa uma tool. Devolve o content-block MCP. Fail-closed em cada porta."""
    try:
        cap = contrato.capacidade(nome)
    except KeyError:
        return {"content": [{"type": "text", "text": f"tool '{nome}' não existe"}],
                "isError": True}
    if cap.escopo == Escopo.ESCRITA:
        # a parede: escrita não executa por MCP — devolve a cerimônia, não o efeito
        return {"content": [{"type": "text", "text":
                "ESCRITA exige aprovação humana (HITL) — o MCP não a executa. "
                f"Frase de confirmação declarada no contrato: «{cap.confirmacao}»"}],
                "isError": True}
    if cap.modo != ModoLeitura.NETWORK_JSON:
        return {"content": [{"type": "text", "text":
                f"'{nome}' é modo DOM (playwright_read) — ainda não servido por este endpoint"}],
                "isError": True}
    from foral.executor import executar_leitura
    try:
        out = executar_leitura(tenant_id, contrato.sistema, nome, args or {})
    except PermissionError as e:
        return {"content": [{"type": "text", "text": f"recusado: {e}"}], "isError": True}
    except FileNotFoundError as e:
        return {"content": [{"type": "text", "text": f"não encontrado: {e}"}], "isError": True}
    except RuntimeError as e:
        return {"content": [{"type": "text", "text": f"sessão: {e}"}], "isError": True}
    except (ValueError, KeyError) as e:
        return {"content": [{"type": "text", "text": f"erro: {e}"}], "isError": True}
    if out.get("alarme"):
        # HEALING visível: shape mudou → avisa o agente/dev antes de dado torto entrar
        aviso = ("⚠ FINGERPRINT ALARM — the system's shape changed (missing: "
                 + ", ".join(out["alarme"]) + "). The contract may be stale; run `foral update`.")
        return {"content": [{"type": "text", "text": aviso + "\n" +
                             json.dumps(out, ensure_ascii=False)}]}
    return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}


def tratar_requisicao(contrato: Contrato, tenant_id: str, req: dict) -> Optional[dict]:
    """O núcleo JSON-RPC 2.0 do MCP — puro e testável. None = notificação (sem resposta)."""
    metodo = req.get("method")
    rid = req.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def erro(code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    if metodo == "initialize":
        proto = (req.get("params") or {}).get("protocolVersion") or PROTO_PADRAO
        return ok({
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": f"foral-{contrato.sistema.lower()}", "version": "0.1.0"},
        })
    if metodo in ("notifications/initialized", "initialized"):
        return None                                   # notificação: sem resposta
    if metodo == "ping":
        return ok({})
    if metodo == "tools/list":
        return ok({"tools": tools_do_contrato(contrato)})
    if metodo == "tools/call":
        params = req.get("params") or {}
        nome = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return erro(-32602, "arguments tem de ser objeto")
        return ok(_executar_tool(contrato, tenant_id, str(nome), args))
    if rid is None:
        return None                                   # notificação desconhecida: ignora
    return erro(-32601, f"método não suportado: {metodo}")


def servir(contrato: Contrato, tenant_id: str,
           entrada=None, saida=None) -> None:
    """Laço stdio JSON-RPC (uma linha por mensagem). Wrapper fino sobre o núcleo."""
    entrada = entrada or sys.stdin
    saida = saida or sys.stdout
    for linha in entrada:
        linha = linha.strip()
        if not linha:
            continue
        try:
            req = json.loads(linha)
        except ValueError:
            continue                                   # linha inválida: ignora (robustez)
        resp = tratar_requisicao(contrato, tenant_id, req)
        if resp is not None:
            saida.write(json.dumps(resp, ensure_ascii=False) + "\n")
            saida.flush()
