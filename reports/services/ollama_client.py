import os
import requests
from typing import Dict, List, Any

OLLAMA_SERVER = os.environ.get("ollama_server", "http://host.docker.internal:11434")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "600"))
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:32b")

def ollama_chat(
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    think: bool = False,
    model: str | None = None,
) -> str:
    url = f"{OLLAMA_SERVER}/api/chat"
    payload: Dict[str, Any] = {
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "think": think,
    }

    resp = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=OLLAMA_TIMEOUT,
    )

    # Si falla HTTP, se  imprime el body
    if resp.status_code >= 400:
        print("[OLLAMA] status:", resp.status_code)
        print("[OLLAMA] body:", resp.text[:800])

        raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:2000]}")
        

    # Parse JSON
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"Ollama devolvió no-JSON: {resp.text[:2000]}")

    
    msg = data.get("message") or {}
    content = msg.get("content")

    if content is None:
        content = data.get("response")

    if not content or not str(content).strip():
        raise RuntimeError(
            "Ollama devolvió content vacío. "
            f"Revisa modelo/think/prompt. Respuesta completa: {str(data)[:2000]}"
        )

    return str(content)

