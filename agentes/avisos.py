"""Avisos al usuario mediante issues de GitHub: GitHub los manda por email automáticamente."""

from __future__ import annotations

import os

import requests


class GitHub:
    def __init__(self):
        self.repo = os.environ.get("GITHUB_REPOSITORY", "")
        self.propietario = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
        self.sesion = requests.Session()
        self.sesion.headers.update({
            "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
            "Accept": "application/vnd.github+json",
        })
        self.activo = bool(self.repo and os.environ.get("GITHUB_TOKEN"))

    def crear_issue(self, titulo: str, cuerpo: str, etiquetas: list[str]) -> int | None:
        if not self.activo:
            print(f"[aviso sin GitHub] {titulo}")
            return None
        r = self.sesion.post(f"https://api.github.com/repos/{self.repo}/issues", json={
            "title": titulo, "body": cuerpo, "labels": etiquetas,
            "assignees": [self.propietario] if self.propietario else [],
        }, timeout=30)
        r.raise_for_status()
        return r.json()["number"]

    def estado_issue(self, numero: int) -> tuple[str, str | None]:
        """Devuelve (state, state_reason): ('closed', 'completed') = aprobada; ('closed', 'not_planned') = rechazada."""
        if not self.activo:
            return "open", None
        r = self.sesion.get(f"https://api.github.com/repos/{self.repo}/issues/{numero}", timeout=30)
        r.raise_for_status()
        datos = r.json()
        return datos["state"], datos.get("state_reason")
