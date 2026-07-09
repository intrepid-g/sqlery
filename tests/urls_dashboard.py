"""Test-only URLconf that mounts both Django admin and the sqlery namespace.

Used by the dashboard regression test so that `{% url "admin:..." %}` and
`{% url "sqlery:..." %}` resolve the way they do in a real Django project — where
admin is registered for ScheduledTask but NOT for the composite-PK QueuedJob.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("sqlery.django_sqlery.urls", namespace="sqlery")),
]
