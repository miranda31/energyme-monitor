"""
MCP Server – accès LLM aux logs EnergyMe via Graylog REST API.
Expose 3 outils : search_logs, get_recent_logs, get_log_summary.
"""

import base64
import os

import httpx
from mcp.server.fastmcp import FastMCP

GRAYLOG_HOST     = os.environ.get("GRAYLOG_HOST",     "localhost")
GRAYLOG_PORT     = int(os.environ.get("GRAYLOG_PORT", "9000"))
GRAYLOG_USERNAME = os.environ.get("GRAYLOG_USERNAME", "admin")
GRAYLOG_PASSWORD = os.environ.get("GRAYLOG_PASSWORD", "admin")

BASE_URL = f"http://{GRAYLOG_HOST}:{GRAYLOG_PORT}"

mcp = FastMCP("energyme-logs")


def _auth_header() -> str:
    token = base64.b64encode(f"{GRAYLOG_USERNAME}:{GRAYLOG_PASSWORD}".encode()).decode()
    return f"Basic {token}"


def _headers() -> dict:
    return {
        "Authorization": _auth_header(),
        "Accept":        "application/json",
        "X-Requested-By": "mcp-server",
    }


def _format_messages(hits: list) -> str:
    lines = []
    for h in hits:
        src   = h.get("source", {})
        ts    = src.get("timestamp", "?")
        level = src.get("level", "?")
        msg   = src.get("message", "")
        lines.append(f"[{ts}] [{level}] {msg}")
    return "\n".join(lines) if lines else "(aucun résultat)"


@mcp.tool()
async def search_logs(
    query: str,
    severity: str = "",
    since_minutes: int = 60,
    limit: int = 50,
) -> str:
    """
    Recherche dans les logs EnergyMe stockés dans Graylog.

    Args:
        query:         Requête de recherche (syntaxe Lucene, ex: "wifi OR ade7953")
        severity:      Filtre optionnel sur le niveau (VERBOSE/DEBUG/INFO/WARNING/ERROR/FATAL)
        since_minutes: Fenêtre temporelle en minutes (par défaut 60)
        limit:         Nombre maximum de messages retournés (max 500)
    """
    q = query
    if severity:
        q = f"({q}) AND level:{severity.upper()}"

    params = {
        "query":  q,
        "range":  since_minutes * 60,
        "limit":  min(limit, 500),
        "sort":   "timestamp:desc",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/api/search/universal/relative",
            headers=_headers(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

    messages = data.get("messages", [])
    total    = data.get("total_results", len(messages))
    result   = _format_messages(messages)
    return f"Résultats : {len(messages)}/{total}\n\n{result}"


@mcp.tool()
async def get_recent_logs(n: int = 50) -> str:
    """
    Retourne les N derniers messages de log EnergyMe.

    Args:
        n: Nombre de messages à retourner (max 500)
    """
    params = {
        "query": "*",
        "range": 86400,  # 24h
        "limit": min(n, 500),
        "sort":  "timestamp:desc",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/api/search/universal/relative",
            headers=_headers(),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

    messages = data.get("messages", [])
    return _format_messages(messages)


@mcp.tool()
async def get_log_summary() -> str:
    """
    Retourne un résumé des logs par niveau sur les dernières 24 heures.
    Utile pour avoir une vue d'ensemble rapide de l'état du système.
    """
    levels = ["VERBOSE", "DEBUG", "INFO", "WARNING", "ERROR", "FATAL"]
    summary_lines = ["Résumé des logs EnergyMe – 24 dernières heures\n"]

    async with httpx.AsyncClient() as client:
        for level in levels:
            params = {
                "query": f"level:{level}",
                "range": 86400,
                "limit": 0,
            }
            try:
                resp = await client.get(
                    f"{BASE_URL}/api/search/universal/relative",
                    headers=_headers(),
                    params=params,
                    timeout=10,
                )
                resp.raise_for_status()
                count = resp.json().get("total_results", 0)
                summary_lines.append(f"  {level:<10}: {count:>6} messages")
            except Exception:
                summary_lines.append(f"  {level:<10}: erreur lors de la requête")

    return "\n".join(summary_lines)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
