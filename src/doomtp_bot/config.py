"""Settings from environment, .env and Docker secrets (architecture §3.7)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from doomtp_bot.lang.parser import DEFAULT_PREFIX

#: Where the password login is taken from unless ADMIN_PASSWORD_NETWORKS says otherwise: this host and
#: the private ranges. The password is the way in when Twitch is down, not a second front door.
LOCAL_NETWORKS = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"  # conventions:allow-infra


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    twitch_client_id: str | None = None
    twitch_client_secret: SecretStr | None = None
    twitch_client_secret_file: Path | None = None
    # Expected Twitch user ID of the bot account; /auth/callback rejects tokens for any other account.
    twitch_bot_id: str | None = None

    # Comma-separated Twitch user IDs; parsed by `bot_owner_ids`.
    bot_owner_ids_csv: str = Field(default="", validation_alias="BOT_OWNER_IDS")

    data_dir: Path = Path("./data")

    # Postgres (ADR-0014). One database, schemas `bot` and `chatlog`. The password is kept out of the
    # URL so it can come from a Docker secret, the same way the Twitch and admin secrets do.
    database_url: str = "postgresql://doomtp@postgres:5432/doomtp"
    database_password: SecretStr | None = None
    database_password_file: Path | None = None

    web_host: str = "127.0.0.1"
    web_port: int = 8080
    public_base_url: str = "http://localhost:8080"
    # The public pages can be opened by chat at PUBLIC_BASE_URL (a reverse proxy, a tunnel). Off by
    # default, because the web UI is LAN-only until then; on, `!explain` links to its full report.
    public_web_ui: bool = False
    # The public web site (doomtp-web), the same for every channel. When set, `!help` ends with a link to
    # the channel's command page there. Unset, `!help` names no page.
    web_site_url: str | None = None
    # Sites on other origins that may read /api/v2/twitch/users, the chat user cards' profiles (ADR-0035):
    # comma-separated, e.g. https://vods.example,https://archive.example. Same-origin sites need nothing.
    user_lookup_origins: str = ""
    # Comma-separated addresses (or "*") of reverse proxies whose X-Forwarded-For/-Proto uvicorn believes.
    # The default trusts only a proxy on the same host; anything else would let a client pick its address.
    web_forwarded_allow_ips: str = "127.0.0.1"

    default_prefix: str = DEFAULT_PREFIX

    # The admin password: the web site's way in that doesn't need Twitch. Read from the environment (.env)
    # or a file. Without one, the password login is off rather than open.
    admin_password: SecretStr | None = None
    admin_password_file: Path | None = None
    # Where the password login is taken from: comma-separated addresses or CIDRs, or `*` for anywhere.
    # Default: this host and the private ranges.
    admin_password_networks: str = LOCAL_NETWORKS
    # Who signs moderators in to the web admin (ADR-0023): Twitch directly, or vex-auth (the sign-in
    # shared by the vexoul.net sites), which then needs its URL and this bot's client registration there.
    signin_provider: Literal["twitch", "vex"] = "twitch"
    vex_auth_url: str | None = None  # as browsers reach it
    vex_auth_internal_url: str | None = None  # as the bot reaches it; unset = vex_auth_url
    vex_auth_client_id: str = "dtp"
    vex_auth_client_secret: SecretStr | None = None
    vex_auth_client_secret_file: Path | None = None
    ivr_logs_url: str = "https://logs.ivr.fi"  # where chat history is backfilled from (ADR-0008)

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"

    @property
    def bot_owner_ids(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.bot_owner_ids_csv.split(",") if p.strip())

    def client_secret(self) -> str | None:
        """TWITCH_CLIENT_SECRET, or the contents of TWITCH_CLIENT_SECRET_FILE."""
        return _secret(self.twitch_client_secret, self.twitch_client_secret_file)

    def vex_auth_secret(self) -> str | None:
        """VEX_AUTH_CLIENT_SECRET, or the contents of VEX_AUTH_CLIENT_SECRET_FILE."""
        return _secret(self.vex_auth_client_secret, self.vex_auth_client_secret_file)

    def admin_password_value(self) -> str | None:
        """ADMIN_PASSWORD, or the contents of ADMIN_PASSWORD_FILE."""
        return _secret(self.admin_password, self.admin_password_file)

    def database_dsn(self) -> str:
        """DATABASE_URL with DATABASE_PASSWORD (or the contents of DATABASE_PASSWORD_FILE) spliced in.

        Returned rather than stored so the password never sits on the Settings object, where a repr in
        a log line or a traceback would print it.
        """
        password = _secret(self.database_password, self.database_password_file)
        if password is None:
            return self.database_url
        parts = urlsplit(self.database_url)
        if parts.password:  # already carries one; don't quietly override what the operator wrote
            return self.database_url
        userinfo = quote(parts.username or "", safe="") + ":" + quote(password, safe="")
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit(parts._replace(netloc=f"{userinfo}@{host}"))

    @property
    def secure_cookies(self) -> bool:
        """Cookies are `Secure` once the site is reached over https, whether or not the proxy is trusted."""
        return self.public_base_url.lower().startswith("https://")

    def forwarding_undoes_password_networks(self) -> bool:
        """WEB_FORWARDED_ALLOW_IPS trusts every peer while the admin password is limited to some networks.
        Then any client can claim a private address in X-Forwarded-For, and the limit means nothing."""
        trusts_all = "*" in {p.strip() for p in self.web_forwarded_allow_ips.split(",")}
        limited = "*" not in {p.strip() for p in self.admin_password_networks.split(",")}
        return trusts_all and limited and self.admin_password_value() is not None

    @property
    def lock_path(self) -> Path:
        return self.data_dir / ".lock"


def _secret(value: SecretStr | None, file: Path | None) -> str | None:
    """A secret given inline, else read from its file; blank counts as not set."""
    if value is not None:
        return value.get_secret_value() or None
    if file and file.is_file():
        return file.read_text(encoding="utf-8").strip() or None
    return None
