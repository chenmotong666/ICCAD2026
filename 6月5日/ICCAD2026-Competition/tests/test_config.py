from pathlib import Path

from config import load_config


def test_load_openai_config_with_placeholder_uses_env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                'provider: "openai"',
                "openai:",
                '  api_key: "<YOUR_OPENAI_API_KEY>"',
                '  base_url: "https://example.com/v1"',
                '  model: "gpt-test"',
                "generation:",
                "  temperature: 0.1",
                "  max_output_tokens: 128",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    cfg = load_config(str(cfg_path))

    assert cfg.llm.provider == "openai"
    assert cfg.llm.api_key == "test-key"
    assert cfg.llm.base_url == "https://example.com/v1"
    assert cfg.llm.model == "gpt-test"
    assert cfg.llm.temperature == 0.1
    assert cfg.llm.max_output_tokens == 128


def test_example_config_has_no_real_key():
    text = Path("config.example.yaml").read_text(encoding="utf-8")

    assert "sk-" not in text
    assert "<YOUR_OPENAI_API_KEY>" in text
