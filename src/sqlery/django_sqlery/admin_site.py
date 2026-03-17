"""Custom Django admin site for SQLery with unified dashboard."""

from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe


class SQLeryAdminSite(admin.AdminSite):
    """Custom admin site for SQLery with unified dashboard.

    This admin site provides a single unified dashboard entry while hiding
    individual model admin pages from the index. Models remain accessible
    via direct URLs for CRUD operations.
    """

    site_header = "SQLery"
    site_title = "SQLery"
    index_title = "SQLery Dashboard"
    index_template = "admin/sqlery/admin_index.html"

    def get_app_list(self, request):
        """Override to hide ScheduledTask and QueuedJob from admin index.

        Models remain accessible via direct URLs for CRUD operations,
        but won't appear in the main admin index list.
        """
        app_list = super().get_app_list(request)

        # Filter out sqlery models from the app list
        for app in app_list:
            if app['app_label'] == 'sqlery':
                # Keep the app but remove the models from index display
                app['models'] = []

        return app_list

    def index(self, request, extra_context=None):
        """Override index to add dashboard URL to context."""
        extra_context = extra_context or {}

        # Add dashboard link to context
        extra_context['dashboard_url'] = reverse('sqlery:dashboard')

        return super().index(request, extra_context)


# Create the custom admin site instance
sqlery_admin = SQLeryAdminSite(name='sqlery_admin')
