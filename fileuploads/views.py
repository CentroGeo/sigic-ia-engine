from rest_framework.response import Response
from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt


@api_view(["GET", "POST"])
#@csrf_exempt
def homeUpload(request):
    return JsonResponse({"message": "upload File!"})