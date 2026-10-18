"""Deploy Lore to a Hugging Face Docker Space (free CPU tier).

Usage:  python scripts/deploy_hf.py <space-name>      (needs `huggingface_hub` login)
The Space builds the Dockerfile: FalkorDB + API/UI/MCP in one container, the
graph rebuilt from HERB at startup.
"""
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi, whoami

ROOT = Path(__file__).resolve().parents[1]
SPACE_README = """---
title: Lore — Company Brain on FalkorDB
emoji: 🧠
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: cc-by-nc-4.0
---

Lore: a company brain whose every answer is a path through a FalkorDB graph.
Source: https://github.com/Petsteb/lore-company-brain
"""


def main(name: str = "lore-company-brain"):
    user = whoami()["name"]
    repo_id = f"{user}/{name}"
    api = HfApi()
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "README.md").write_text(SPACE_README, encoding="utf-8")
        api.upload_file(path_or_fileobj=str(Path(tmp) / "README.md"), path_in_repo="README.md",
                        repo_id=repo_id, repo_type="space")
    api.upload_folder(folder_path=str(ROOT), repo_id=repo_id, repo_type="space",
                      allow_patterns=["Dockerfile", "requirements.txt", "lore/*.py", "web/*", "scripts/*.sh",
                                      "data-derived/decisions/*"],
                      ignore_patterns=["**/__pycache__/**"])
    print(f"https://huggingface.co/spaces/{repo_id}  ->  https://{user.lower()}-{name}.hf.space")


if __name__ == "__main__":
    main(*sys.argv[1:])
