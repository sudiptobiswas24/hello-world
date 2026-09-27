from django.urls import path

from .views import Gstr1JsonView, Gstr1View, Gstr3bView

urlpatterns = [
    path("gstr1/", Gstr1View.as_view()),
    path("gstr1/json/", Gstr1JsonView.as_view()),
    path("gstr3b/", Gstr3bView.as_view()),
]
