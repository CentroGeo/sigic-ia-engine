from django.urls import path
from reports.views import generate_pptx_report

urlpatterns = [
    path("pptx/", generate_pptx_report),
]