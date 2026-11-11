"""Verify the Supabase-issued access token. Passwords never touch our backend.

Only explicitly allowed algorithms are accepted (so an attacker cannot send alg=none).
Newer Supabase projects sign with asymmetric keys (ES256/RS256, verified through the JWKS URL);
older projects use a shared HS256 secret.
"""
import jwt


class AuthError(Exception):
    pass


_jwks_clients: dict = {}


def decode_token(token: str, jwt_secret: str = "", jwks_url: str = "", audience: str = "authenticated") -> dict:
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256":
            if not jwt_secret:
                raise AuthError("Server is not configured for HS256 tokens")
            key, algorithms = jwt_secret, ["HS256"]
        elif alg in ("ES256", "RS256"):
            if not jwks_url:
                raise AuthError("Server is not configured with a Supabase URL")
            client = _jwks_clients.setdefault(jwks_url, jwt.PyJWKClient(jwks_url, cache_keys=True))
            key, algorithms = client.get_signing_key_from_jwt(token).key, [alg]
        else:
            raise AuthError("Unsupported token algorithm")
        return jwt.decode(token, key, algorithms=algorithms, audience=audience,
                          options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid token: {exc}") from exc
