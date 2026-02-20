"""
SSH Client for remote file operations on the Jellyfin server.

Connects via SSH (password or key-based) and provides:
- Finding .m3u/.m3u8 files under a given path
- Deleting specific files
- Triggering a Jellyfin library rescan via systemctl / jellyfin-cli
"""
from __future__ import annotations

import os
from typing import Optional

import paramiko


class SSHClient:
    def __init__(
        self,
        hostname: str,
        username: str,
        port: int = 22,
        password: Optional[str] = None,
        key_path: Optional[str] = None,
    ):
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = dict(
            hostname=hostname,
            port=port,
            username=username,
            timeout=15,
        )
        if key_path:
            key_path = os.path.expanduser(key_path)
            connect_kwargs["key_filename"] = key_path
        elif password:
            connect_kwargs["password"] = password
        else:
            # Try the SSH agent / default keys (~/.ssh/id_*)
            connect_kwargs["look_for_keys"] = True

        self._client.connect(**connect_kwargs)

    def _run(self, cmd: str) -> tuple[str, str]:
        """Run a remote command; return (stdout, stderr)."""
        _, stdout, stderr = self._client.exec_command(cmd)
        return stdout.read().decode(), stderr.read().decode()

    def find_playlist_files(self, music_path: str) -> list[str]:
        """Return sorted list of absolute .m3u/.m3u8 paths under music_path."""
        music_path = music_path.rstrip("/")
        stdout, _ = self._run(
            f"find {_q(music_path)} -type f \\( -iname '*.m3u' -o -iname '*.m3u8' \\) | sort"
        )
        return [l.strip() for l in stdout.splitlines() if l.strip()]

    def delete_files(self, paths: list[str]) -> dict[str, Optional[str]]:
        """
        Delete each path. Returns dict of path -> error message (or None on success).
        Uses rm -f so the app user needs read/write on those files.
        """
        results: dict[str, Optional[str]] = {}
        for path in paths:
            _, stderr = self._run(f"rm -f {_q(path)} && echo OK")
            err = stderr.strip()
            results[path] = err if err else None
        return results

    def whoami(self) -> str:
        stdout, _ = self._run("whoami")
        return stdout.strip()

    def test_write(self, path: str) -> bool:
        """Return True if the remote user can write to the directory containing path."""
        directory = os.path.dirname(path)
        stdout, _ = self._run(f"test -w {_q(directory)} && echo yes || echo no")
        return stdout.strip() == "yes"

    def close(self) -> None:
        self._client.close()


def _q(path: str) -> str:
    """Shell-quote a path (handles spaces)."""
    return "'" + path.replace("'", "'\\''") + "'"
