from django.urls import path

from .pages import report_page, station_page

urlpatterns = [
    path("<str:code>/", station_page, name="station-page"),
    path("<str:code>/report/", report_page, name="station-report-page"),
]
