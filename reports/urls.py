from django.urls import path
from reports.views import generate_report, list_reports, get_report, delete_report

urlpatterns = [
    path("generate/", generate_report),
    path("", list_reports),
    path("<int:pk>/", get_report),
    path("<int:pk>/delete/", delete_report),
]
