import jwt
import requests
from jwt import InvalidTokenError
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose.utils import base64url_decode
import base64


# KEYCLOAK_ISSUER = "http://localhost:9000/realms/django-app"
# JWKS_URL_INTERNAL = "http://keycloak:8080/realms/django-app/protocol/openid-connect/certs"
# AUDIENCE = "django-api"

KEYCLOAK_REALM = 'sigic'
KEYCLOAK_SERVER_URL = 'https://iam.dev.geoint.mx'
AUDIENCE = 'sigic_geonode'

JWKS_URL_INTERNAL = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
KEYCLOAK_ISSUER = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}"

def refresh_keycloak_token(refresh_token: str) -> str:
    """Intenta refrescar el token de Keycloak utilizando el refresh_token del usuario."""
    if not refresh_token:
        return None
        
    import os
    url = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    client_secret = os.environ.get("KEYCLOAK_CLIENT_SECRET", "ZxYwEJCemFXK6xnWLKi6kwHnTR1Iq98O")
    data = {
        "grant_type": "refresh_token",
        "client_id": "sigic-nuxt-dev",
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }
    try:
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            return f"Bearer {response.json().get('access_token')}"
        else:
            print(f"Error refreshing Keycloak token: {response.text}")
    except Exception as e:
        print(f"Exception refreshing token: {e}")
    return None


def jwk_to_pem(jwk_dict):
    exponent_bytes = base64url_decode(jwk_dict['e'].encode('utf-8'))
    public_exponent = int.from_bytes(exponent_bytes, byteorder='big')

    modulus_bytes = base64url_decode(jwk_dict['n'].encode('utf-8'))
    modulus = int.from_bytes(modulus_bytes, byteorder='big')

    rsa_public_key = rsa.RSAPublicNumbers(public_exponent, modulus).public_key()

    pem_public_key_bytes = rsa_public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    return pem_public_key_bytes

class TokenUser:
    def __init__(self, payload):
        self.payload = payload
        self.is_authenticated = True 

    def __str__(self):
        return self.payload.get("email", "no-email")

def get_public_key(kid):
    jwks = requests.get(JWKS_URL_INTERNAL).json()
    for key in jwks['keys']:
        if key['kid'] == kid:
            return jwk_to_pem(key)
    raise Exception("No se encontró la clave pública")

class KeycloakAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        


        token = auth_header.split(" ")[1]
        print("TOKEN RECIBIDO:", repr(token))
        
        try:
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header["kid"]
            public_key = get_public_key(kid)
            
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=AUDIENCE,
                issuer=KEYCLOAK_ISSUER,
                options={"verify_aud": False}
            )
        except Exception as e:
            raise AuthenticationFailed(f"Token inválido: {str(e)}")

        print("DATA!!!", payload)
        
        user = TokenUser(payload)
        return (user, None)

    def authenticate_header(self, request):
        return 'Bearer realm="sigic"'
