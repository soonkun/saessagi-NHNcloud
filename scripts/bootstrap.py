#!/usr/bin/env python3
"""bootstrap.py — Cross-platform setup script (Windows / macOS / Linux).

Usage:
  Windows : scripts\\bootstrap.cmd   (or: python scripts\\bootstrap.py)
  Mac/Linux: bash scripts/bootstrap.sh  (or: python3 scripts/bootstrap.py)
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ── helpers ────────────────────────────────────────────────────────────────

RESET = "\033[0m"
CYAN = "\033[0;36m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
GRAY = "\033[0;37m"
RED = "\033[0;31m"

IS_WIN = platform.system() == "Windows"


def c(color: str, msg: str) -> str:
    if IS_WIN:
        return msg  # Windows CMD doesn't reliably support ANSI
    return f"{color}{msg}{RESET}"


def info(msg: str) -> None:
    print(c(CYAN, msg))


def ok(msg: str) -> None:
    print(c(GREEN, f"  -> {msg}"))


def warn(msg: str) -> None:
    print(c(YELLOW, f"  {msg}"))


def skip(msg: str) -> None:
    print(c(GRAY, f"  {msg} -- skipping"))


def die(msg: str) -> None:
    print(c(RED, f"\n[ERROR] {msg}"))
    sys.exit(1)


def run(*cmd: str, check: bool = True, capture: bool = False) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        list(cmd),
        check=check,
        capture_output=capture,
        text=True,
    )


def which(name: str) -> bool:
    return shutil.which(name) is not None


# ── entrypoint ─────────────────────────────────────────────────────────────

def main() -> None:
    # Always run from project root
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)

    # Corporate SSL proxy: trust OS certificate store
    os.environ["UV_NATIVE_TLS"] = "1"

    print()
    print("=== AI Assistant Bootstrap ===")
    print(f"    platform : {platform.system()} {platform.machine()}")
    print(f"    python   : {sys.version.split()[0]}")
    print(f"    root     : {root}")
    print()

    # ── 0. uv ──────────────────────────────────────────────────────────────
    info("[0/6] Checking uv...")
    if which("uv"):
        skip("uv already installed")
    else:
        warn("Installing uv...")
        if IS_WIN:
            run("powershell", "-ExecutionPolicy", "ByPass",
                "-c", "irm https://astral.sh/uv/install.ps1 | iex")
        else:
            run("sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh")
        if not which("uv"):
            die("uv installation failed. Restart your terminal and try again.")
        ok("uv installed")
    print()

    # ── 1. upstream clone ──────────────────────────────────────────────────
    info("[1/6] Open-LLM-VTuber upstream...")
    upstream = root / "upstream" / "Open-LLM-VTuber"
    if (upstream / ".git").exists():
        skip(f"{upstream} already exists")
    else:
        if upstream.exists():
            shutil.rmtree(upstream)
        run("git", "clone",
            "https://github.com/Open-LLM-VTuber/Open-LLM-VTuber.git",
            str(upstream))
        ok(f"cloned to {upstream}")

    # frontend is a git submodule -- initialize if missing
    if not (upstream / "frontend" / "index.html").exists():
        warn("Initializing frontend submodule...")
        run("git", "-C", str(upstream), "submodule", "update", "--init", "--recursive")
        ok("frontend submodule initialized")
    print()

    # ── 2. Ollama / Gemma4 ─────────────────────────────────────────────────
    info("[2/6] Ollama models...")
    if which("ollama"):
        result = run("ollama", "list", capture=True, check=False)
        if "gemma4" in result.stdout:
            skip("gemma4:e4b already exists")
        else:
            warn("Downloading gemma4:e4b (~9 GB, this will take a while)...")
            run("ollama", "pull", "gemma4:e4b")
            ok("gemma4:e4b done")
    else:
        warn("Ollama not installed. Install manually:")
        if IS_WIN:
            warn("  https://ollama.com/download/windows")
        elif platform.system() == "Darwin":
            warn("  brew install ollama  OR  https://ollama.com/download/mac")
        else:
            warn("  curl -fsSL https://ollama.com/install.sh | sh")
        warn("Then run: ollama pull gemma4:e4b")
    print()

    # ── 3. Python venv ─────────────────────────────────────────────────────
    info("[3/6] Python virtual environment...")
    venv = root / ".venv"
    venv_python = venv / ("Scripts" if IS_WIN else "bin") / ("python.exe" if IS_WIN else "python")
    if venv_python.exists():
        skip(".venv already exists")
    else:
        if venv.exists():
            shutil.rmtree(venv)
        run("uv", "venv")
        ok(".venv created")
    print()

    # ── 4. Python packages ─────────────────────────────────────────────────
    info("[4/6] Python packages (uv sync)...")
    if (root / "pyproject.toml").exists():
        run("uv", "sync")
        ok("packages installed")
        # melotts conflicts with pypinyin version in pyproject.toml -- install separately
        warn("Installing MeloTTS (TTS engine)...")
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as _f:
            _f.write("pypinyin==0.50.0\n")
            _override = _f.name
        try:
            run("uv", "pip", "install", "--quiet",
                "melotts @ git+https://github.com/myshell-ai/MeloTTS.git",
                "--override", _override)
            ok("MeloTTS installed")
        finally:
            _os.unlink(_override)
    else:
        warn("pyproject.toml not found -- installing dev tools only")
        run("uv", "pip", "install", "ruff", "mypy", "pytest", "pytest-cov")
        (root / "src").mkdir(exist_ok=True)
        (root / "tests").mkdir(exist_ok=True)
        ok("dev tools installed")
    print()

    # ── 5. BGE-M3 embedding model ──────────────────────────────────────────
    info("[5/6] BGE-M3 embedding model (~1.5 GB)...")
    bge_dir = root / "assets" / "models" / "bge-m3"
    if (bge_dir / "config.json").exists():
        skip(f"BGE-M3 already exists ({bge_dir})")
    else:
        bge_dir.mkdir(parents=True, exist_ok=True)
        warn("Installing huggingface_hub + truststore into venv...")
        # truststore: makes Python requests trust the OS (Windows) certificate store,
        # which includes the corporate SSL proxy certificate
        run("uv", "pip", "install", "--quiet", "huggingface_hub", "truststore")
        warn(f"Downloading BAAI/bge-m3 -> {bge_dir} (this will take a while)...")
        run(
            str(venv_python),
            "-c",
            (
                "import truststore; truststore.inject_into_ssl(); "
                "from huggingface_hub import snapshot_download; "
                f"snapshot_download('BAAI/bge-m3', local_dir={str(bge_dir)!r}, "
                "local_dir_use_symlinks=False)"
            ),
        )
        ok("BGE-M3 downloaded")
    print()

    # ── 6. git init (starter kit only) ────────────────────────────────────
    info("[6/6] git...")
    if (root / ".git").exists():
        skip(".git already exists")
    else:
        run("git", "init")
        run("git", "add",
            "README.md", "REQUIREMENTS.md", "CLAUDE.md", "PROJECT_PLAN.md",
            ".gitignore", ".claude/", "prompts/", "docs/", "scripts/",
            "specs/.gitkeep", "reviews/.gitkeep", "upstream/.gitkeep",
            "assets/", "pyproject.toml")
        run("git", "commit", "-m", "chore: initial starter kit")
        ok("git repository initialized")

    print()
    print("========================================")
    print("Bootstrap complete!")
    print("========================================")
    print()

    if (root / "src" / "app" / "main.py").exists():
        print("To start the server:")
        sep = ";" if IS_WIN else ":"
        print(f'  set PYTHONPATH=src{sep}upstream/Open-LLM-VTuber/src{sep}upstream/Open-LLM-VTuber')
        print('  uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 12393')
        print()
        print("Open http://127.0.0.1:12393 in your browser.")
    else:
        print("Next steps:")
        print("  1. Run Claude Code in this folder: claude")
        print("  2. Paste the prompt from prompts/00_kickoff.md")
    print()


if __name__ == "__main__":
    main()
