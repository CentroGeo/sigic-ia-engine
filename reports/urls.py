from django.urls import path
from reports.views import generate_pptx_report, generate_report, list_reports, get_report

urlpatterns = [
    path("pptx/", generate_pptx_report),
    path("generate/", generate_report),
    path("", list_reports),
    path("<int:pk>/", get_report),
]
