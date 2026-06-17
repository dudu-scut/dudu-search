"""LDAP 认证客户端 — 连接、bind、用户属性查询。"""

from dataclasses import dataclass

from app.config import settings
from app.logging_config import get_logger

logger = get_logger("ldap")


@dataclass
class LDAPUser:
    username: str
    email: str | None = None
    display_name: str | None = None


class LDAPClient:
    """LDAP 认证客户端（配置为空则不启用）。"""

    @staticmethod
    def is_enabled() -> bool:
        return bool(settings.LDAP_URL and settings.LDAP_BASE_DN)

    @staticmethod
    def authenticate(username: str, password: str) -> LDAPUser | None:
        """
        尝试 LDAP bind 认证。

        步骤:
        1. 连接 LDAP Server
        2. 构建用户 DN: {attr}={username},{user_rdn},{base_dn}
        3. bind(user_dn, password)
        4. 成功后搜索用户属性返回 LDAPUser
        5. 失败返回 None
        """
        if not LDAPClient.is_enabled():
            return None
        if not username or not password:
            return None

        try:
            from ldap3 import Server, Connection, Tls
            from ldap3.utils.conv import escape_filter_chars

            use_tls = settings.LDAP_USE_TLS
            server = Server(
                settings.LDAP_URL,
                connect_timeout=5,
                tls=Tls() if use_tls else None,
            )

            user_dn = f"{settings.LDAP_USERNAME_ATTR}={username},{settings.LDAP_USER_RDN},{settings.LDAP_BASE_DN}"

            conn = Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=False,
                receive_timeout=5,
            )

            try:
                if not conn.bind():
                    logger.info("LDAP bind failed", username=username)
                    return None

                # Bind 成功，搜索用户属性
                email = None
                display_name = None
                if settings.LDAP_EMAIL_ATTR or settings.LDAP_USERNAME_ATTR:
                    search_dn = (
                        f"{settings.LDAP_USER_RDN},{settings.LDAP_BASE_DN}"
                        if settings.LDAP_USER_RDN
                        else settings.LDAP_BASE_DN
                    )
                    attrs = [settings.LDAP_EMAIL_ATTR] if settings.LDAP_EMAIL_ATTR else []
                    safe_username = escape_filter_chars(username)
                    if conn.search(
                        search_base=search_dn,
                        search_filter=f"({settings.LDAP_USERNAME_ATTR}={safe_username})",
                        attributes=attrs,
                        size_limit=1,
                        time_limit=3,
                    ):
                        for entry in conn.entries:
                            email_val = getattr(entry, settings.LDAP_EMAIL_ATTR, None)
                            if email_val:
                                email = str(email_val)

                logger.info("LDAP authenticate success", username=username, email=email)
                return LDAPUser(username=username, email=email, display_name=display_name)

            finally:
                conn.unbind()

        except Exception as exc:
            logger.warning("LDAP authenticate error", username=username, error=str(exc))
            return None
