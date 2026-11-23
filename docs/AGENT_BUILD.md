# Building the Cortex Ingest Agent

The agent (`CortexIngest.exe`) is a PyInstaller bundle of `cortex_agent/`,
the cortex-intake engine and their dependencies. A bundle carries its own
copies of everything, so it only gets security fixes when it is rebuilt.
The build is pinned so that a rebuild is a deliberate, repeatable act.

## What pins what

| File | Role |
|---|---|
| `requirements-agent.txt` | loose runtime deps (edit this one) |
| `requirements-agent-build.txt` | loose build and test tools (PyInstaller, pytest) |
| `requirements-agent.lock.txt` | the exact set a build installs, with hashes (generated) |
| `cortex_agent.spec` | what PyInstaller puts in the bundle |

The interpreter is part of the bundle too (`python311.dll` and OpenSSL).
Builds use the CPython 3.11 patch release named in the commands below,
fetched by uv. python.org stopped publishing Windows installers for 3.11
at 3.11.9, so a plain "Python 3.11" install on Windows is years behind.

## Build

Needs uv 0.13 or newer and a checkout of cortex-intake.

```powershell
uv venv .venv-agent --python 3.11.18
uv pip sync --python .venv-agent\Scripts\python.exe --require-hashes requirements-agent.lock.txt
uv pip install --python .venv-agent\Scripts\python.exe --no-deps <path to cortex-intake>
.venv-agent\Scripts\python.exe -m pytest tests\test_agent_update.py -q
.venv-agent\Scripts\python.exe -m PyInstaller cortex_agent.spec --noconfirm
```

The result is `dist\CortexIngest\`. `CortexIngest.exe --help` and
`--once` (one sweep to stdout, no tray) are the quick checks.

## Refreshing the pins

Do this on a schedule and whenever an advisory lands on a bundled package.

```powershell
uv pip compile requirements-agent.txt requirements-agent-build.txt --python-version 3.11 --python-platform windows --generate-hashes --exclude-newer <a week ago, as YYYY-MM-DDT00:00:00Z> -o requirements-agent.lock.txt
uvx --from pip-audit==2.10.1 pip-audit --vulnerability-service osv --require-hashes --disable-pip -r requirements-agent.lock.txt
```

`--exclude-newer` keeps releases younger than a week out of the lock: a
bad upload is usually caught within days, and the date makes the same
resolution repeatable. Move it past a release only when that release is
the fix you need. Bump the CPython patch in the build commands and in
`.github/workflows/build-agent.yml` at the same time, then rebuild and
reinstall: a refreshed lock changes nothing on a machine until the new
exe is on it.
