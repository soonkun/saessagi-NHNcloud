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


# upstream을 고정할 알려진 정상 커밋. patches/ 는 이 커밋 기준으로 적용된다.
UPSTREAM_PINNED_COMMIT = "19b58b1"


def apply_upstream_patches(root: Path, upstream: Path) -> None:
    """patches/*.patch 를 upstream clone에 적용 (멱등).

    upstream은 참조용 외부 의존성이라 직접 수정하지 않는 게 원칙이지만,
    대화 루프 내부처럼 외부 후크가 없어 EXTEND(상속·래핑)로 풀 수 없는
    소수의 수정은 여기 patch로 관리한다. 이렇게 하면 clone/재clone 후에도
    패치가 보존되고(재설치 시 조용히 유실되지 않음), 무결성 테스트의
    baseline과도 일치한다. 자세한 사유는 patches/README.md 참조.
    """
    patch_dir = root / "patches"
    if not patch_dir.is_dir():
        return
    for patch in sorted(patch_dir.glob("*.patch")):
        # 이미 적용됐으면(reverse-check 성공) 건너뜀 — 멱등
        already = run(
            "git",
            "-C",
            str(upstream),
            "apply",
            "--reverse",
            "--check",
            str(patch),
            check=False,
            capture=True,
        )
        if already.returncode == 0:
            skip(f"upstream patch already applied: {patch.name}")
            continue
        run("git", "-C", str(upstream), "apply", str(patch))
        ok(f"applied upstream patch: {patch.name}")


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
            run(
                "powershell",
                "-ExecutionPolicy",
                "ByPass",
                "-c",
                "irm https://astral.sh/uv/install.ps1 | iex",
            )
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
        run("git", "clone", "https://github.com/Open-LLM-VTuber/Open-LLM-VTuber.git", str(upstream))
        # 재현성: 알려진 정상 커밋에 고정 (patches/ 가 이 커밋 기준)
        run("git", "-C", str(upstream), "checkout", "--quiet", UPSTREAM_PINNED_COMMIT)
        ok(f"cloned to {upstream} @ {UPSTREAM_PINNED_COMMIT}")

    # 사내 필수 upstream 패치 적용 (멱등 — 기존 clone에도 안전)
    apply_upstream_patches(root, upstream)

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
        # melotts: install pre-built tokenizers first to avoid Rust compilation,
        # then install melotts without its strict old tokenizers/transformers pins.
        warn("Installing MeloTTS (TTS engine, may take a while)...")
        try:
            run(
                "uv",
                "pip",
                "install",
                "--quiet",
                "tokenizers>=0.14.0",
                "transformers>=4.35.0",
                "pypinyin==0.50.0",
            )
            run(
                "uv",
                "pip",
                "install",
                "--quiet",
                "--no-deps",
                "melotts @ git+https://github.com/myshell-ai/MeloTTS.git",
            )
            ok("MeloTTS installed")
        except Exception:
            warn("MeloTTS installation failed (likely missing Rust compiler).")
            warn("Voice output will be disabled. Text chat still works.")
            warn("To enable TTS later, install Rust from https://rustup.rs and re-run bootstrap.")
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
        run(
            "git",
            "add",
            "README.md",
            "REQUIREMENTS.md",
            "CLAUDE.md",
            "PROJECT_PLAN.md",
            ".gitignore",
            ".claude/",
            "prompts/",
            "docs/",
            "scripts/",
            "specs/.gitkeep",
            "reviews/.gitkeep",
            "upstream/.gitkeep",
            "assets/",
            "pyproject.toml",
        )
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
        print(f"  set PYTHONPATH=src{sep}upstream/Open-LLM-VTuber/src{sep}upstream/Open-LLM-VTuber")
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
