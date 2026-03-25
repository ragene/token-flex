"""
setup.py — legacy setuptools shim for token-flow-service.

Canonical config lives in pyproject.toml; this file exists so that
`pip install -e .` works on older pip versions and to expose the
`tf-server` CLI entry point explicitly.

CLI commands registered:
  tf-server          → token_flow._cli:main  (start/stop/restart/status/distill/poller/install-service/uninstall-service)

Post-install:
  Automatically installs and starts the appropriate OS service:
    Linux   → systemd user service
    macOS   → launchd user agent
    Windows → Windows Task Scheduler task
"""
import subprocess
import sys
from setuptools import setup, find_packages
from setuptools.command.install import install
from setuptools.command.develop import develop


def _run_install_service():
    """Invoke tf-server install-service after the package is installed."""
    try:
        subprocess.run(
            [sys.executable, "-m", "token_flow._cli_runner", "install-service"],
            check=False,
        )
    except Exception as e:
        print(f"⚠️  Post-install service setup failed (non-fatal): {e}", file=sys.stderr)
        print("    Run manually: tf-server install-service", file=sys.stderr)


class PostInstall(install):
    def run(self):
        super().run()
        _run_install_service()


class PostDevelop(develop):
    def run(self):
        super().run()
        _run_install_service()


setup(
    cmdclass={
        "install": PostInstall,
        "develop": PostDevelop,
    },
    name="token-flow-service",
    version="0.1.0",
    description="token-flow — local memory distillation and token tracking service for OpenClaw.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    python_requires=">=3.9",
    packages=find_packages(
        where=".",
        include=["token_flow*", "api*", "db*", "engine*"],
        exclude=["aws*", "scripts*", "token-flow-ui*", "*.egg-info*"],
    ),
    install_requires=[
        "fastapi>=0.111.0",
        "uvicorn[standard]>=0.29.0",
        "anthropic>=0.25.0",
        "boto3>=1.34.0",
        "tiktoken>=0.7.0",
        "python-dotenv>=1.0.0",
        "psycopg2-binary>=2.9.9",
        "python-jose[cryptography]>=3.3.0",
        "httpx>=0.27.0",
    ],
    extras_require={
        "dev": ["pytest", "httpx"],
    },
    entry_points={
        "console_scripts": [
            # Start / stop / manage the local token-flow service
            "tf-server = token_flow._cli:main",
        ],
    },
    include_package_data=True,
    package_data={"*": ["*.json", "*.yaml", "*.yml", "*.md"]},
    license="MIT",
)
