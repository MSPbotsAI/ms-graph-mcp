from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mcp_http_port: int = 8080
    mcp_http_host: str = "0.0.0.0"

    # Overridable for sovereign clouds (GCC High, DoD, China 21Vianet)
    graph_base_url: str = "https://graph.microsoft.com/v1.0"

    # Hosts graph_send_mail may fetch a content_url attachment from (comma
    # separated, exact hostnames, https only). Defaults to the Agent platform,
    # whose public file links need no login (PRD-20707). Empty disables
    # content_url entirely.
    attachment_fetch_allowed_hosts: str = "agent.mspbots.ai,agentint.mspbots.ai"


def get_settings() -> Settings:
    return Settings()
