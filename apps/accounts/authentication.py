from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .services import find_valid_token


class BearerTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer <token>`. Missing header -> anonymous; bad/expired/revoked -> 401."""

    keyword = b"bearer"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword:
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Invalid authorization header.")
        try:
            raw = parts[1].decode()
        except UnicodeError:
            raise AuthenticationFailed("Invalid token.") from None
        token = find_valid_token(raw)
        if token is None:
            raise AuthenticationFailed("Invalid or expired token.")
        return token.user, token

    def authenticate_header(self, request):
        return "Bearer"
