from __future__ import annotations

import time
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import requests


class XtreamError(RuntimeError):
    pass


class AuthenticationError(XtreamError):
    pass


MAX_API_RESPONSE_BYTES = 64 * 1024 * 1024


def normalize_server_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    if not candidate.startswith(("http://", "https://")):
        candidate = f"http://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise XtreamError("Use uma URL de servidor HTTP ou HTTPS válida.")
    if parsed.username or parsed.password or any(ord(char) < 32 for char in candidate):
        raise XtreamError("A URL base não pode conter credenciais ou caracteres de controle.")
    return candidate


@dataclass(slots=True)
class XtreamClient:
    server_url: str
    username: str
    password: str
    timeout: tuple[int, int] = (8, 30)

    def __post_init__(self) -> None:
        self.server_url = normalize_server_url(self.server_url)

    def _request(self, action: str | None = None, **params: Any) -> Any:
        payload = {"username": self.username, "password": self.password, **params}
        if action:
            payload["action"] = action
        try:
            response = requests.get(
                f"{self.server_url}/player_api.php",
                params=payload,
                timeout=self.timeout,
                stream=True,
            )
            with response:
                if response.status_code >= 400:
                    raise XtreamError(f"O servidor recusou a solicitação (HTTP {response.status_code}).")
                content_length = response.headers.get("Content-Length", "")
                if content_length.isdigit() and int(content_length) > MAX_API_RESPONSE_BYTES:
                    raise XtreamError("O catálogo retornado excede o limite seguro de 64 MB.")
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_content(256 * 1024):
                    received += len(chunk)
                    if received > MAX_API_RESPONSE_BYTES:
                        raise XtreamError("O catálogo retornado excede o limite seguro de 64 MB.")
                    chunks.append(chunk)
                data = json.loads(b"".join(chunks))
        except XtreamError:
            raise
        except requests.Timeout as exc:
            raise XtreamError("O servidor demorou demais para responder.") from exc
        except requests.RequestException as exc:
            raise XtreamError("Não foi possível conectar ao servidor. Confira a URL e sua rede.") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise XtreamError("O servidor retornou uma resposta inválida.") from exc

        if isinstance(data, dict) and data.get("user_info", {}).get("auth") in (0, "0"):
            raise AuthenticationError("Credenciais inválidas ou conta inativa.")
        return data

    def test_connection(self) -> dict[str, Any]:
        start = time.monotonic()
        data = self._request()
        latency_ms = int((time.monotonic() - start) * 1000)
        user = data.get("user_info", {}) if isinstance(data, dict) else {}
        server_info = data.get("server_info", {}) if isinstance(data, dict) else {}
        if user.get("auth") not in (1, "1"):
            raise AuthenticationError("Credenciais inválidas ou conta inativa.")
        
        result = dict(user)
        result["latency_ms"] = latency_ms
        result["server_info"] = server_info
        return result

    def get_vod_streams(self) -> list[dict[str, Any]]:
        data = self._request("get_vod_streams")
        return data if isinstance(data, list) else []

    def get_vod_categories(self) -> list[dict[str, Any]]:
        data = self._request("get_vod_categories")
        return data if isinstance(data, list) else []

    def get_series(self) -> list[dict[str, Any]]:
        data = self._request("get_series")
        return data if isinstance(data, list) else []

    def get_series_categories(self) -> list[dict[str, Any]]:
        data = self._request("get_series_categories")
        return data if isinstance(data, list) else []

    def get_series_info(self, series_id: int | str) -> dict[str, Any]:
        data = self._request("get_series_info", series_id=series_id)
        if not isinstance(data, dict):
            raise XtreamError("Detalhes da série em formato inesperado.")
        return data

    def get_live_streams(self) -> list[dict[str, Any]]:
        data = self._request("get_live_streams")
        return data if isinstance(data, list) else []

    def get_live_categories(self) -> list[dict[str, Any]]:
        data = self._request("get_live_categories")
        return data if isinstance(data, list) else []

    def get_short_epg(self, stream_id: int | str, limit: int = 6) -> list[dict[str, Any]]:
        try:
            data = self._request("get_short_epg", stream_id=stream_id, limit=limit)
            if isinstance(data, dict) and "epg_listings" in data:
                return data["epg_listings"]
        except Exception:
            pass
        return []

    def stream_urls(self, kind: str, stream_id: int | str, extension: str | None) -> list[str]:
        if kind == "live":
            folder = "live"
        elif kind == "movie":
            folder = "movie"
        else:
            folder = "series"

        user = quote(self.username, safe="")
        password = quote(self.password, safe="")
        base = f"{self.server_url}/{folder}/{user}/{password}/{stream_id}"
        clean_ext = str(extension or "").strip().lstrip(".")
        return [f"{base}.{clean_ext}", base] if clean_ext else [base]
