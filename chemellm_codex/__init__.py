"""Codex MCP adapter for the independent ChemELLM kernel."""

__version__ = "0.6.0"


def skill_fingerprint(folder):
    """Fingerprint instructions and supporting resources as one package."""
    import hashlib
    digest = hashlib.sha256()
    paths = (p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for path in sorted(paths, key=lambda p:p.relative_to(folder).as_posix()):
        digest.update(path.relative_to(folder).as_posix().encode('utf-8')+b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
