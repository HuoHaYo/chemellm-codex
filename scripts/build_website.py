"""Package only public website files and the verified, published installer."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import tarfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
INSTALLER = f"ChemELLM-Codex-{VERSION}-windows-x64.zip"
PUBLIC_FILES = ("index.html", "styles.css", "app.js", "favicon.svg", "robots.txt", "sitemap.xml")
DEPLOY_FILES = ("nginx.conf", "publish.sh", "chemellm-site-renew.service", "chemellm-site-renew.timer")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, default=ROOT / "dist" / INSTALLER)
    parser.add_argument("--checksums", type=Path, help="Release SHA256SUMS.txt; defaults to the installer's directory")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "deployment" / "website.tar.gz")
    args = parser.parse_args()
    if args.installer.name != INSTALLER:
        raise SystemExit(f"Expected installer for this website version: {INSTALLER}")
    checksum_path = args.checksums or args.installer.parent / "SHA256SUMS.txt"
    matches = [line.split() for line in checksum_path.read_text(encoding="utf-8").splitlines()
               if len(line.split()) == 2 and line.split()[1] == INSTALLER]
    if len(matches) != 1 or len(matches[0][0]) != 64:
        raise SystemExit("Release checksums must identify exactly one matching installer")
    installer_sha256 = matches[0][0].lower()
    installer = args.installer.read_bytes()
    if hashlib.sha256(installer).hexdigest() != installer_sha256:
        raise SystemExit("Installer does not match Release SHA256SUMS.txt")
    public = {name: (ROOT / "site" / name).read_bytes() for name in PUBLIC_FILES}
    if INSTALLER.encode() not in public["index.html"] or f"v{VERSION}".encode() not in public["index.html"]:
        raise SystemExit("Website version and installer links must match the package")
    public[f"downloads/{INSTALLER}"] = installer
    public["downloads/SHA256SUMS.txt"] = f"{installer_sha256}  {INSTALLER}\n".encode()
    hashes = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(public.items()))
    files = {f"public/{name}": data for name, data in public.items()}
    files["SHA256SUMS"] = hashes.encode()
    for name in DEPLOY_FILES:
        files[name] = (ROOT / "deploy" / "website" / name).read_bytes().replace(b"\r\n", b"\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    built_at = int(time.time())
    with tarfile.open(args.output, "w:gz") as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            # Nginx uses mtime and size for validators; each build needs fresh mtimes.
            info.mtime = built_at
            archive.addfile(info, io.BytesIO(data))
    print(f"Built {args.output} ({args.output.stat().st_size} bytes)")
    print(f"Installer SHA256: {installer_sha256}")
    print(f"Archive SHA256: {hashlib.sha256(args.output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
