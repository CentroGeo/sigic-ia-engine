#!/usr/bin/env python
"""
Script para actualizar permisos de todos los documentos sin permisos públicos
IMPORTANTE: Necesitas proporcionar un token de autorización válido
"""
import requests
import os
import sys

def fix_document_permissions(document_id, auth_token):
    """Actualiza permisos de un documento específico"""
    geonode_url = os.getenv('GEONODE_SERVER', 'https://geonode.dev.geoint.mx').rstrip('/')

    # Endpoint para actualizar permisos (GeoNode 4.x)
    url = f"{geonode_url}/api/v2/resources/{document_id}/permissions"

    headers = {
        "Authorization": auth_token,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Hacer el documento público (visible para AnonymousUser)
    payload = {
        "uuid": None,  # Se llenará con el UUID del documento
        "perms": [
            {
                "name": "view",
                "type": "user",
                "avatar": None,
                "permissions": "view",
                "user": {
                    "username": "AnonymousUser",
                    "first_name": "",
                    "last_name": "",
                    "avatar": None,
                    "perms": ["view"]
                }
            }
        ]
    }

    print(f"Actualizando permisos del documento ID {document_id}...")
    print(f"URL: {url}")

    try:
        response = requests.put(url, json=payload, headers=headers)

        if response.status_code in [200, 201, 204]:
            print(f"✓ Permisos actualizados exitosamente")
            return True
        else:
            print(f"✗ Error: {response.status_code}")
            print(f"Respuesta: {response.text[:500]}")
            return False

    except Exception as e:
        print(f"✗ Excepción: {str(e)}")
        return False

if __name__ == "__main__":
    # Obtener el token desde la línea de comandos
    if len(sys.argv) < 2:
        print("\n" + "="*70)
        print("ERROR: Debes proporcionar un token de autorización")
        print("="*70)
        print("\nUso:")
        print(f"  python {sys.argv[0]} 'Bearer tu_token_aqui'")
        print("\nPara obtener tu token:")
        print("  1. Abre las DevTools del navegador (F12)")
        print("  2. Ve a la pestaña 'Network' (Red)")
        print("  3. Haz una petición al backend (ej: lista workspaces)")
        print("  4. Busca el header 'Authorization' en la petición")
        print("  5. Copia el valor completo (debe empezar con 'Bearer ')")
        print()
        sys.exit(1)

    auth_token = sys.argv[1]

    # Verificar que el token tenga el formato correcto
    if not auth_token.startswith('Bearer '):
        print("⚠ Advertencia: El token debe empezar con 'Bearer '")
        auth_token = f"Bearer {auth_token}"
        print(f"Token ajustado: {auth_token[:50]}...")

    print("\nActualizando permisos del documento 408...\n")

    success = fix_document_permissions(408, auth_token)

    if success:
        print("\n" + "="*70)
        print("✓ ¡ÉXITO! El documento ahora debería ser accesible")
        print("="*70)
        print(f"\nVerifica en: https://geonode.dev.geoint.mx/documents/408")
        print()
    else:
        print("\n" + "="*70)
        print("✗ No se pudo actualizar automáticamente")
        print("="*70)
        print("\nAlternativa: Actualizar permisos manualmente en GeoNode:")
        print("  1. Ve a: https://geonode.dev.geoint.mx/documents/408")
        print("  2. Click en 'Change Permissions' o 'Cambiar Permisos'")
        print("  3. Marca 'Anyone can view' o 'Cualquiera puede ver'")
        print("  4. Guarda los cambios")
        print()