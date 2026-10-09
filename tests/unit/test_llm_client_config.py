"""Unit tests verifying base_url propagation into the OpenAI clients."""

from pg_mcp.config.settings import OpenAIConfig, ValidationConfig
from pg_mcp.services.result_validator import ResultValidator
from pg_mcp.services.sql_generator import SQLGenerator


class TestBaseUrlPropagation:
    """Compatible-API base_url must reach the underlying AsyncOpenAI clients."""

    def test_generator_uses_custom_base_url(self) -> None:
        """SQLGenerator passes base_url to its OpenAI client."""
        config = OpenAIConfig(api_key="sk-test", base_url="http://gw.internal:3030/v1")
        generator = SQLGenerator(config)
        assert str(generator.client.base_url).rstrip("/") == "http://gw.internal:3030/v1"

    def test_generator_default_base_url(self) -> None:
        """Without base_url the client targets the official API."""
        config = OpenAIConfig(api_key="sk-test", _env_file=None)
        generator = SQLGenerator(config)
        assert "api.openai.com" in str(generator.client.base_url)

    def test_result_validator_uses_custom_base_url(self) -> None:
        """ResultValidator passes base_url to its OpenAI client."""
        config = OpenAIConfig(api_key="sk-test", base_url="http://gw.internal:3030/v1")
        validator = ResultValidator(config, ValidationConfig())
        assert str(validator.client.base_url).rstrip("/") == "http://gw.internal:3030/v1"
