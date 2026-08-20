#!/usr/bin/env python3
"""Build the Lambda deployment package with dependencies.

Installs runtime dependencies for the Lambda target platform (Linux x86_64)
into a .build/ directory alongside the source code. CDK references this
directory as the Lambda code asset.

Usage:
    python scripts/build_lambda.py
"""

import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAMBDA_SRC = PROJECT_ROOT / "lambda"
BUILD_DIR = PROJECT_ROOT / ".build" / "lambda"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"


def main() -> None:
    """Build the Lambda deployment package."""
    print(f"Building Lambda package in {BUILD_DIR}")

    # Clean previous build
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)

    # Copy source code
    for item in ("deployment_agent", "shared", "event_parser"):
        src = LAMBDA_SRC / item
        dst = BUILD_DIR / item
        if src.exists():
            shutil.copytree(src, dst)
            print(f"  Copied {item}/")

    # Install dependencies for Lambda target platform
    print("  Installing dependencies for linux x86_64...")
    pip_args = [
        "install",
        "-r",
        str(REQUIREMENTS),
        "-t",
        str(BUILD_DIR),
        "--platform",
        "manylinux2014_x86_64",
        "--only-binary=:all:",
        "--python-version",
        "3.13",
        "--implementation",
        "cp",
        "--upgrade",
        "--quiet",
    ]
    from pip._internal.cli.main import main as pip_main

    exit_code = pip_main(pip_args)
    if exit_code != 0:
        raise SystemExit(f"pip install failed with exit code {exit_code}")

    # Remove boto3/botocore (already in Lambda runtime)
    for pkg in (
        "boto3",
        "botocore",
        "s3transfer",
        "urllib3",
        "jmespath",
        "python_dateutil",
        "dateutil",
        "six",
        "certifi",
    ):
        pkg_dir = BUILD_DIR / pkg
        if pkg_dir.exists():
            shutil.rmtree(pkg_dir)
        # Also remove dist-info
        for dist_info in BUILD_DIR.glob(f"{pkg}*dist-info"):
            shutil.rmtree(dist_info)

    # Remove unnecessary files to reduce package size
    for pattern in ("*.pyc", "__pycache__"):
        for path in BUILD_DIR.rglob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    print(f"  Build complete. Package at: {BUILD_DIR}")


if __name__ == "__main__":
    main()
