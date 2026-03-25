from pathlib import Path


def test_dockerfile_copies_legacy_tree_into_runtime_image():
    dockerfile = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")

    assert "COPY legacy/ ./legacy/" in content
