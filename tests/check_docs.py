"""Check that published CLI examples parse; never execute their operations."""
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import openany_hermes

count = 0
image_count = 0
guides = [name for name in ("GUIDE.en.md", "GUIDE.zh-CN.md") if (ROOT / name).is_file()]
assert "GUIDE.en.md" in guides
for name in ("README.md", *guides):
    text = (ROOT / name).read_text()
    assert "API_SERVER" in text or name == "README.md"
    commands = set()
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        for line in block.replace("\\\n", " ").splitlines():
            marker = "scripts/openany_hermes.py "
            if marker not in line or "--help" in line:
                continue
            args = shlex.split(line.split(marker, 1)[1])
            parsed = openany_hermes.parser().parse_args(args)
            commands.add(parsed.command)
            count += 1
    if name in guides:
        assert commands == {"api-config", "check", "https", "verify", "restore"}, (name, commands)
        images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
        assert images, (name, "guide must include its illustrated Dashboard steps")
        for image in images:
            path = (ROOT / image).resolve()
            assert path.is_relative_to(ROOT.resolve()) and path.is_file(), (name, image)
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), (name, "image must be a valid PNG", image)
            image_count += 1
for name in guides:
    text = (ROOT / name).read_text()
    for required in ("API_SERVER_ENABLED", "API_SERVER_HOST", "API_SERVER_PORT", "API_SERVER_KEY", "hermes gateway restart", "hermes gateway install", "--key-file", "certbot renew", "fixed_body"):
        assert required in text, (name, required)
    assert "--require-existing-key --apply" in text, (name, "Dashboard sync must require its saved key")
    assert "ExitOnForwardFailure=yes" in text and "127.0.0.1:19119:127.0.0.1:9119" in text, (name, "SSH tunnel must explicitly bind loopback and fail on conflicts")
print("PASS:", count, "documented CLI examples parsed; API/service/key/renewal coverage present in", ", ".join(guides) + ". No operations executed.")
print("PASS:", image_count, "local screenshot references resolve to packaged PNG files.")
