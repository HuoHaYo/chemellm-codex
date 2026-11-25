"""Codex MCP adapter for the independent ChemELLM kernel."""

__version__ = "0.7.1"


def skill_fingerprint(folder):
    """Fingerprint instructions and supporting resources as one package."""
    import hashlib
    digest = hashlib.sha256()
    paths = (p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for path in sorted(paths, key=lambda p:p.relative_to(folder).as_posix()):
        digest.update(path.relative_to(folder).as_posix().encode('utf-8')+b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def skill_inventory(folder):
    """All discoverable skills, with stable names and independent fingerprints."""
    return [{'name': path.name, 'fingerprint': skill_fingerprint(path)}
            for path in sorted(folder.iterdir(), key=lambda p: p.name)
            if path.is_dir() and (path / 'SKILL.md').is_file()]
