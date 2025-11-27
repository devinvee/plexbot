from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

@dataclass
class NewUserInviteConfig:
    enabled: bool = False
    role_id: Optional[int] = None
    invite_link: Optional[str] = None

@dataclass
class DiscordConfig:
    sonarr_notification_channel_id: str = ""
    radarr_notification_channel_id: str = ""
    dm_notifications_enabled: bool = True
    test_guild_id: int = 0
    new_user_invite: NewUserInviteConfig = field(default_factory=NewUserInviteConfig)

@dataclass
class OverseerrConfig:
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    refresh_interval_minutes: int = 60

@dataclass
class SonarrInstanceConfig:
    name: str = ""
    url: str = ""
    api_key: str = ""
    enabled: bool = False

@dataclass
class UserMappings:
    plex_to_discord: Dict[str, str] = field(default_factory=dict)

@dataclass
class BotConfig:
    log_level: str = "INFO"
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    overseerr: OverseerrConfig = field(default_factory=OverseerrConfig)
    sonarr_instances: List[SonarrInstanceConfig] = field(default_factory=list)
    user_mappings: UserMappings = field(default_factory=UserMappings)
    tmdb: Dict[str, Any] = field(default_factory=dict)

bot_config = BotConfig()

def update_config(new_config: Dict[str, Any]):
    """Updates the global bot_config with a dictionary."""
    global bot_config
    bot_config.log_level = new_config.get("log_level", "INFO")
    
    discord_data = new_config.get("discord", {})
    new_user_invite_data = discord_data.get("new_user_invite", {})
    discord_data["new_user_invite"] = NewUserInviteConfig(**new_user_invite_data)
    bot_config.discord = DiscordConfig(**discord_data)

    bot_config.overseerr = OverseerrConfig(**new_config.get("overseerr", {}))
    bot_config.sonarr_instances = [SonarrInstanceConfig(**instance) for instance in new_config.get("sonarr_instances", [])]
    bot_config.user_mappings = UserMappings(**new_config.get("user_mappings", {}))
    bot_config.tmdb = new_config.get("tmdb", {})
