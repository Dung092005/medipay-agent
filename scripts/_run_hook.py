"""Windows: strip UTF-8 BOM rồi forward sang log_hook.py."""
import subprocess
import sys
from pathlib import Path

raw = sys.stdin.buffer.read()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]

target = Path(__file__).with_name("log_hook.py")
completed = subprocess.run(
    [sys.executable, str(target), *sys.argv[1:]],
    input=raw,
)
sys.exit(completed.returncode)
