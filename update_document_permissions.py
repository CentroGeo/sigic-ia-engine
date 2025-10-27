#!/usr/bin/env python
"""
Script para actualizar los permisos de un documento en GeoNode
"""
import requests
import os
import sys
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'llm.settings.dev')
django.setup()

from fileuploads.models import Files

def update_document_permissions(document_id, authorization_token):
    """
    Actualiza los permisos de un documento en GeoNode para hacerlo público
    """
    geonode_url = os.getenv('GEONODE_SERVER', 'https://geonode.dev.geoint.mx').rstrip('/')

    # URL para actualizar permisos
    permissions_url = f"{geonode_url}/api/v2/documents/{document_id}"

    headers = {
        "Authorization": authorization_token,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Configuración de permisos públicos
    permissions_data = {
        "document": {
            "perms": {
                "users": [
                    {
                        "id": "AnonymousUser",
                        "permissions": "view"
                    }
                ],
                "groups": []
            }
        }
    }

    print(f"Actualizando permisos del documento {document_id}...")
    print(f"URL: {permissions_url}")

    try:
        # Primero, intentar con PATCH
        response = requests.patch(
            permissions_url,
            json=permissions_data,
            headers=headers
        )

        if response.status_code in [200, 201, 204]:
            print(f"✓ Permisos actualizados exitosamente!")
            print(f"  Código de respuesta: {response.status_code}")
            return True
        else:
            print(f"✗ Error al actualizar permisos:")
            print(f"  Código: {response.status_code}")
            print(f"  Respuesta: {response.text}")

            # Intentar método alternativo usando el endpoint de permisos específico
            alt_url = f"{geonode_url}/documents/{document_id}/permissions"
            print(f"\nIntentando método alternativo: {alt_url}")

            alt_data = {
                "permissions": {
                    "users": {
                        "AnonymousUser": ["view_resourcebase"]
                    },
                    "groups": {}
                }
            }

            alt_response = requests.post(
                alt_url,
                json=alt_data,
                headers=headers
            )

            if alt_response.status_code in [200, 201, 204]:
                print(f"✓ Permisos actualizados con método alternativo!")
                return True
            else:
                print(f"✗ Error con método alternativo:")
                print(f"  Código: {alt_response.status_code}")
                print(f"  Respuesta: {alt_response.text}")
                return False

    except Exception as e:
        print(f"✗ Excepción al actualizar permisos: {str(e)}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python update_document_permissions.py <document_id> <authorization_token>")
        print("Ejemplo: python update_document_permissions.py 408 'Bearer eyJ...'")
        sys.exit(1)

    doc_id = int(sys.argv[1])
    auth_token = sys.argv[2]

    # Verificar que el documento existe en la base de datos
    try:
        file_obj = Files.objects.get(geonode_id=doc_id)
        print(f"Documento encontrado en BD:")
        print(f"  ID local: {file_obj.id}")
        print(f"  Filename: {file_obj.filename}")
        print(f"  GeoNode ID: {file_obj.geonode_id}")
        print(f"  UUID: {file_obj.geonode_uuid}")
        print()
    except Files.DoesNotExist:
        print(f"⚠ Advertencia: Documento {doc_id} no encontrado en la base de datos local")
        print()

    success = update_document_permissions(doc_id, auth_token)

    if success:
        print(f"\n✓ Documento {doc_id} ahora debería ser accesible")
        print(f"  Verifica en: https://geonode.dev.geoint.mx/documents/{doc_id}")
        sys.exit(0)
    else:
        print(f"\n✗ No se pudieron actualizar los permisos automáticamente")
        print(f"  Deberás actualizar los permisos manualmente desde la interfaz de GeoNode")
        sys.exit(1)