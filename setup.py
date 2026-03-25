"""
setup.py — legacy setuptools shim for token-flow-service.

Canonical config lives in pyproject.toml; this file exists so that
`pip install -e .` works on older pip versions and to expose the
`tf-server` CLI entry point explicitly.

CLI commands registered:
  tf-server          → token_flow._cli:main  (start/stop/restart/status/distill/poller)
"""
from setuptools import setup, find_packages

setup(
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
