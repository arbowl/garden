"""Default config values, can be overridden by environment variables or garden.toml file."""

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        toml_file="garden.toml",
        env_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    db_path: str = "garden.db"
    sources_path: str = "sources.toml"

    ollama_url: str = "http://localhost:11434"
    ollama_disciplined_model: str = "qwen3:8b"
    ollama_creative_model: str = "rocinante-x:12b"

    hot_rank_gravity: float = 1.8
    curator_threshold: float = 0.4

    fetch_interval_minutes: int = 30
    avatar_session_interval_minutes: int = 10
    max_session_seconds: int = 600

    max_reply_depth: int = 8
    max_replies_per_post: int = 3
    max_post_comments: int = 30
    max_posts_per_source: int = 5


settings = Settings()
