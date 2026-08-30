"""O EXECUTOR de capacidades network_json (27/08/2026).

O agente conhece a CAPACIDADE, não o browser: quem chama diz (sistema,
capacidade, params) e recebe LINHAS tipadas — o dot-path do contrato é aplicado
AQUI, num lugar só. O ganho central: a API interna que a SPA já consome responde
a um GET com o cookie da sessão salva — a capacidade network_json NÃO abre
Chromium; é HTTP puro com a sessão decifrada.

Fail-closed:
· capacidade de ESCRITA recusa aqui (escrita é playwright_action + HITL, outra
  porta — esta executa só leitura);
· o HOST é o do CONTRATO (dominios[0]) — o chamador não escolhe URL;
· sessão ausente/expirada devolve erro claro (não um 200 vazio);
· fingerprint: campo OBRIGATÓRIO ausente em todas as linhas → `alarme` na
  resposta (o shape da fonte mudou — o healing observa isto), nunca dado torto
  entrando calado.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

from foral.contract import Capacidade, Contrato, Escopo, ModoLeitura, carregar_contrato

_CONTRATOS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "contratos")


def contratos_dir() -> str:
    """O contrato REAL é dado de deployment, não do repo: `CONTRATOS_DIR` aponta o diretório
    (fora de qualquer git). Lida a cada chamada de propósito — a env pode mudar entre testes e
    o processo não precisa reiniciar para trocar de deployment."""
    return os.getenv("CONTRATOS_DIR") or _CONTRATOS_DIR


def carregar_contrato_do_sistema(sistema: str) -> Contrato:
    import yaml

    caminho = os.path.join(contratos_dir(), f"{sistema.lower()}.yaml")
    if not os.path.exists(caminho):
        raise FileNotFoundError(f"contrato do sistema '{sistema}' não existe")
    with open(caminho) as f:
        return carregar_contrato(yaml.safe_load(f))


# ── O DOT-PATH (função pura; os shapes são os MEDIDOS no contrato v2) ────────
# Formas suportadas: "jobs[].id" · "jobs[].customer.name" ·
# "jobs[].responsibles[].person.name" (lista interna → lista de valores) ·
# "tabs" (valor direto) · "pagination.total_records" · "[].id" (lista raiz) ·
# "records.*[].id" (wildcard de dict — o kanban agrupa por employee_id).

def extrair_caminho(corpo, caminho: str):
    """Extrai UM caminho do corpo. Listas ([]/*) produzem lista; ausência → None."""
    return _desce(corpo, _passos(caminho))


def _passos(caminho: str) -> list[str]:
    # "records.*[].id" → ["records", "*[]", "id"] · "[].id" → ["[]", "id"]
    partes: list[str] = []
    for p in caminho.split("."):
        if p == "":
            continue
        while p.endswith("[]") and p != "[]":
            # "jobs[]" fica inteiro; só separa quando houver sufixo composto
            break
        partes.append(p)
    return partes


def _desce(no, passos: list[str]):
    if no is None:
        return None
    if not passos:
        return no
    p, resto = passos[0], passos[1:]
    if p == "[]":
        if not isinstance(no, list):
            return None
        return [_desce(item, resto) for item in no]
    if p == "*[]" or p == "*":
        # wildcard de DICT: achata os valores (cada valor é lista de itens)
        if not isinstance(no, dict):
            return None
        itens = []
        for v in no.values():
            if isinstance(v, list):
                itens.extend(v)
            else:
                itens.append(v)
        return [_desce(item, resto) for item in itens]
    if p.endswith("[]"):
        chave = p[:-2]
        v = no.get(chave) if isinstance(no, dict) else None
        if not isinstance(v, list):
            return None
        return [_desce(item, resto) for item in v]
    return _desce(no.get(p) if isinstance(no, dict) else None, resto)


def _prefixo_de_lista(caminho: str) -> tuple[str, str] | None:
    """('jobs[]', 'id') para 'jobs[].id'; ('records.*[]', 'id') para o kanban;
    ('[]', 'id') para lista raiz. None quando o caminho não itera lista."""
    for marca in ("[].", "*[]."):
        idx = caminho.find(marca)
        if idx >= 0:
            corte = idx + len(marca) - 1  # inclui o [] no prefixo, sem o ponto
            return caminho[:corte], caminho[corte + 1:]
    return None


def linhas_da_capacidade(corpo, cap: Capacidade) -> dict:
    """Aplica TODOS os extratores: os que compartilham o prefixo de lista do
    id_na_fonte viram COLUNAS das linhas; os demais viram `meta`. Devolve
    {linhas, meta, alarme} — alarme lista campos OBRIGATÓRIOS ausentes."""
    ext_id = next(e for e in cap.extratores if e.campo == cap.id_na_fonte)
    pref = _prefixo_de_lista(ext_id.caminho or "")
    linhas: list[dict] = []
    meta: dict = {}
    if pref is None:
        # capacidade de valor único (ex.: metricas_producao): uma "linha" só
        linha = {}
        for e in cap.extratores:
            linha[e.campo] = extrair_caminho(corpo, e.caminho or "")
        linhas = [linha]
    else:
        prefixo, _ = pref
        itens = extrair_caminho(corpo, prefixo)
        itens = itens if isinstance(itens, list) else []
        colunas: list[tuple[str, str, bool]] = []  # (campo, resto-do-caminho, obrig)
        for e in cap.extratores:
            p = _prefixo_de_lista(e.caminho or "")
            if p and p[0] == prefixo:
                colunas.append((e.campo, p[1], e.obrigatorio))
            else:
                meta[e.campo] = extrair_caminho(corpo, e.caminho or "")
        for item in itens:
            linhas.append({campo: _desce(item, _passos(resto)) for campo, resto, _ in colunas})
    # fingerprint: obrigatório ausente em TODAS as linhas → o shape mudou.
    alarme = []
    for e in cap.extratores:
        if not e.obrigatorio:
            continue
        if e.campo in meta:
            if meta[e.campo] is None:
                alarme.append(e.campo)
        elif linhas and all(l.get(e.campo) is None for l in linhas):
            alarme.append(e.campo)
    return {"linhas": linhas, "meta": meta, "alarme": alarme}


# ── A EXECUÇÃO (HTTP com a sessão salva; sem browser) ────────────────────────

def _base_url(dominio: str) -> str:
    """Sandbox/dev (29/08/2026): `FORAL_HOSTS_MAP` mapeia o host DO CONTRATO para uma
    base local (ex.: {"demo.exemplo.dev": "http://127.0.0.1:8123"}). O chamador continua
    sem escolher URL — o mapa é ambiente da INSTÂNCIA; produção simplesmente não o
    define. Fail-closed: mapa malformado é erro de operação com mensagem clara, nunca
    um fallback calado para o default."""
    mapa_env = os.getenv("FORAL_HOSTS_MAP")
    if mapa_env:
        try:
            mapa = json.loads(mapa_env)
        except ValueError as e:
            raise RuntimeError(f"FORAL_HOSTS_MAP malformado: {e}")
        base = mapa.get(dominio)
        if base:
            base = str(base).rstrip("/")
            # red-team 29/08: nunca mandar o Cookie de sessão por http NÃO-loopback
            # (downgrade interceptável). http só em 127.0.0.1/localhost (dev/sandbox).
            p = urllib.parse.urlparse(base)
            if p.scheme == "http" and (p.hostname or "") not in ("127.0.0.1", "localhost", "::1"):
                raise RuntimeError(f"FORAL_HOSTS_MAP com http não-loopback ({base}) — recusado")
            if p.scheme not in ("http", "https"):
                raise RuntimeError(f"FORAL_HOSTS_MAP com esquema inválido: {base}")
            return base
    return f"https://{dominio}"


_MAX_CORPO = 25 * 1024 * 1024   # teto de leitura da resposta (anti-OOM; red-team 29/08)


def _e_ip_privado(host: str) -> bool:
    """True se o host resolve para loopback/privado/link-local — o alvo clássico de SSRF
    (127/8, 169.254/16 metadados de nuvem, 10/8, 172.16/12, 192.168/16, ::1)."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for fam, *_rest, sa in infos:
        try:
            ip = ipaddress.ip_address(sa[0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _opener_seguro(hosts_ok: set[str], permitir_loopback: bool):
    """Opener que RECUSA redirect para host fora da allowlist do contrato (senão o Cookie
    de sessão vaza cross-origin e vira SSRF — red-team 29/08). Loopback só quando o host
    foi explicitamente mapeado (dev/sandbox via FORAL_HOSTS_MAP)."""
    class _SemRedirectHostil(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            h = urllib.parse.urlparse(newurl).hostname or ""
            if h not in hosts_ok:
                raise urllib.error.HTTPError(
                    newurl, code, f"redirect para host fora da allowlist: {h}", headers, fp)
            if not permitir_loopback and _e_ip_privado(h):
                raise urllib.error.HTTPError(
                    newurl, code, f"redirect para IP privado bloqueado: {h}", headers, fp)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(_SemRedirectHostil)


def _caminho_sistema(tenant_id: str, sistema: str) -> str:
    """Onde a sessão cifrada do (tenant, sistema) vive na INSTÂNCIA do cliente
    (SESSION_DATA_DIR, fora de qualquer git)."""
    d = os.path.join(os.getenv("SESSION_DATA_DIR", "./sessions"), tenant_id, sistema)
    os.makedirs(d, exist_ok=True)
    return d


def _cookies_da_sessao(tenant_id: str, sistema: str, dominio: str) -> str:
    from foral.crypto import decrypt_state

    state_path = os.path.join(_caminho_sistema(tenant_id, sistema), "state.json")
    if not os.path.exists(state_path):
        raise RuntimeError(f"sem sessão salva para (tenant, {sistema}) — conecte pelo cartão")
    state = json.loads(decrypt_state(open(state_path, "rb").read()).decode("utf-8"))
    par = [
        f"{c['name']}={c['value']}"
        for c in state.get("cookies", [])
        if dominio.endswith(c.get("domain", "").lstrip(".")) or c.get("domain", "").lstrip(".") in dominio
    ]
    if not par:
        raise RuntimeError(f"sessão salva não tem cookie para {dominio}")
    return "; ".join(par)


def executar_leitura(tenant_id: str, sistema: str, nome_capacidade: str,
                     params: dict | None = None) -> dict:
    """Executa UMA capacidade network_json de LEITURA e devolve {linhas, meta,
    alarme, status}. Escrita e playwright_read recusam — outras portas."""
    contrato = carregar_contrato_do_sistema(sistema)
    cap = contrato.capacidade(nome_capacidade)
    if cap.escopo == Escopo.ESCRITA:
        raise PermissionError(f"'{nome_capacidade}' é ESCRITA — o executor só lê (HITL é outra porta)")
    if cap.modo != ModoLeitura.NETWORK_JSON:
        raise ValueError(f"'{nome_capacidade}' é {cap.modo.value} — este executor cobre network_json")
    dominio = contrato.dominios[0]
    rota = cap.rota if cap.rota.startswith("/") else "/" + cap.rota
    # a rota do contrato pode já carregar query fixa (ex.: ?search[status]=tabs)
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(rota).query))
    caminho = urllib.parse.urlparse(rota).path
    # parâmetros de PATH ({id},{id2}…): entram NO CAMINHO, não na query — é o que faz
    # get_record(id) ser reutilizável p/ Ana, Bruno e Clara. Fail-closed: placeholder sem
    # argumento correspondente é erro (a capacidade EXIGE o parâmetro), nunca URL torta.
    no_path = set()
    for ph in re.findall(r"\{(\w+)\}", caminho):
        if not params or ph not in params:
            raise ValueError(f"'{nome_capacidade}' exige o parâmetro de path '{ph}'")
        caminho = caminho.replace("{" + ph + "}",
                                  urllib.parse.quote(str(params[ph]), safe=""))
        no_path.add(ph)
    fixas = set(query)   # chaves que a ROTA do contrato fixou (o fence de escopo)
    for k, v in (params or {}).items():
        if str(k) in no_path:
            continue        # já consumido no caminho — não repetir na query
        # red-team 29/08: params do chamador ADICIONAM, mas NUNCA sobrescrevem uma chave
        # fixada pela rota do contrato (senão scope=meus vira scope=todos — IDOR).
        if str(k) in fixas:
            raise ValueError(f"param '{k}' colide com a query fixa do contrato — recusado")
        query[str(k)] = str(v)
    base = _base_url(dominio)
    url = base + caminho
    if query:
        url += "?" + urllib.parse.urlencode(query)
    # SSRF: fora de um host-map explícito (dev/sandbox), recusa host que resolve p/ IP
    # privado — o browser/execução nunca alcança 127.0.0.1/metadados de nuvem/rede interna.
    mapeado = bool(os.getenv("FORAL_HOSTS_MAP"))
    if not mapeado and _e_ip_privado(dominio):
        raise RuntimeError(f"host {dominio} resolve para IP privado — bloqueado (SSRF)")
    req = urllib.request.Request(url, headers={
        "Cookie": _cookies_da_sessao(tenant_id, sistema, dominio),
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })
    opener = _opener_seguro(set(contrato.dominios), permitir_loopback=mapeado)
    with opener.open(req, timeout=30) as r:
        status = r.status
        corpo = json.loads(r.read(_MAX_CORPO))
    out = linhas_da_capacidade(corpo, cap)
    out["status"] = status
    return out


def ler_direto(contrato: Contrato, nome_capacidade: str, cookie_header: str,
               params: dict | None = None, base_url: str | None = None) -> dict:
    """Leitura BROWSERLESS com o contrato + cookies EM MEMÓRIA (nada em disco — o T0 não é
    persistido). É a PROVA: o agente lê pelo contrato, HTTP puro, SEM navegador. Mesmos
    fences do executar_leitura (só GET/network_json; {id} no path; IDOR; SSRF; fingerprint)."""
    cap = contrato.capacidade(nome_capacidade)
    if cap.escopo == Escopo.ESCRITA:
        raise PermissionError(f"'{nome_capacidade}' é ESCRITA — o executor só lê")
    if cap.modo != ModoLeitura.NETWORK_JSON:
        raise ValueError(f"'{nome_capacidade}' é {cap.modo.value} — só network_json")
    dominio = contrato.dominios[0]
    rota = cap.rota if cap.rota.startswith("/") else "/" + cap.rota
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(rota).query))
    caminho = urllib.parse.urlparse(rota).path
    no_path = set()
    for ph in re.findall(r"\{(\w+)\}", caminho):
        if not params or ph not in params:
            raise ValueError(f"'{nome_capacidade}' exige o parâmetro de path '{ph}'")
        caminho = caminho.replace("{" + ph + "}", urllib.parse.quote(str(params[ph]), safe=""))
        no_path.add(ph)
    fixas = set(query)
    for k, v in (params or {}).items():
        if str(k) in no_path:
            continue
        if str(k) in fixas:
            raise ValueError(f"param '{k}' colide com a query fixa do contrato — recusado")
        query[str(k)] = str(v)
    base = (base_url or _base_url(dominio)).rstrip("/")
    url = base + caminho + ("?" + urllib.parse.urlencode(query) if query else "")
    mapeado = bool(base_url) or bool(os.getenv("FORAL_HOSTS_MAP"))
    if not mapeado and _e_ip_privado(dominio):
        raise RuntimeError(f"host {dominio} resolve para IP privado — bloqueado (SSRF)")
    req = urllib.request.Request(url, headers={
        "Cookie": cookie_header, "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    })
    opener = _opener_seguro(set(contrato.dominios), permitir_loopback=mapeado)
    with opener.open(req, timeout=30) as r:
        status = r.status
        corpo = json.loads(r.read(_MAX_CORPO))
    out = linhas_da_capacidade(corpo, cap)
    out["status"] = status
    out["url"] = url
    return out
