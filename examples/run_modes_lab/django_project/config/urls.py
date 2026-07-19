"""URL configuration for run_modes_lab."""
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path

from sqlery.django_sqlery.views import trigger_view


def healthz_view(request):
    """Simple health check endpoint."""
    return HttpResponse("ok")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz_view),
    path("internal/trigger/", trigger_view),
]
