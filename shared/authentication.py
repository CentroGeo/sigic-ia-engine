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
        return self.payload.get("preferred_username", "unknown")

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

        user = TokenUser(payload)
        return (user, None)
