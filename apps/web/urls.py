from django.urls import path, re_path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(url="/app/", permanent=False)),
    path("app/", views.shell, name="web-shell"),
    re_path(r"^app/(?P<path>.*)$", views.shell),
]
