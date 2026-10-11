"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.contrib.auth.views import PasswordResetView
from django.urls import include, path

from apps.core.users_api import TrustedEmailResetForm

urlpatterns = [
    path("admin/", admin.site.urls),
    # Before auth.urls: no reset mail to an email whose setter was out-ranked since (O157).
    path("accounts/password_reset/", PasswordResetView.as_view(form_class=TrustedEmailResetForm),
         name="password_reset"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("station/", include("apps.manufacturing.pages_urls")),
    path("api/core/", include("apps.core.urls")),
    path("api/inventory/", include("apps.inventory.urls")),
    path("api/accounting/", include("apps.accounting.urls")),
    path("api/sales/", include("apps.sales.urls")),
    path("api/purchasing/", include("apps.purchasing.urls")),
    path("api/hr/", include("apps.hr.urls")),
    path("api/manufacturing/", include("apps.manufacturing.urls")),
    path("api/quality/", include("apps.quality.urls")),
    path("api/planning/", include("apps.planning.urls")),
    path("api/assets/", include("apps.assets.urls")),
    path("api/gst/", include("apps.gst.urls")),
    path("api/imports/", include("apps.imports.urls")),
    path("api/web/", include("apps.web.api_urls")),
    path("", include("apps.web.urls")),
]
