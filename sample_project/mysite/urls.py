"""URL configuration for sample project."""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('', RedirectView.as_view(url='/admin/', permanent=False)),
    # path('', include('sqlery.urls')),  # Old location - moved to django_sqlery
    path('', include('sqlery.django_sqlery.urls')),
    path('admin/', admin.site.urls),
]
