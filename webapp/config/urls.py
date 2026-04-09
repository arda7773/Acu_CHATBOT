from django.contrib import admin
from django.urls import path, include
from scraper.admin_views import system_stats_view

urlpatterns = [
    path('admin/system-stats/', system_stats_view, name='system_stats'),
    path('admin/', admin.site.urls),
    path('', include('chat.urls')),
]
