"""Execute from /tmp inside a built image to verify the installed wheel matches source."""
import hashlib
import json
from pathlib import Path
import app

source = Path("/app/backend/app")
installed = Path(app.__file__).resolve().parent
if installed == source or not installed.is_relative_to(Path("/app/backend/.venv")):
    raise RuntimeError("Artifact verification must import the installed package from outside the source directory")
files = sorted(source.rglob("*.py"))
digest = hashlib.sha256()
for file in files:
    relative = file.relative_to(source)
    packaged = installed / relative
    if not packaged.is_file() or file.read_bytes() != packaged.read_bytes():
        raise RuntimeError(f"Installed application differs from image source: {relative}")
    digest.update(relative.as_posix().encode())
    digest.update(file.read_bytes())
print(json.dumps({"installed_application_matches_source": True, "python_files": len(files), "source_sha256": digest.hexdigest()}))
