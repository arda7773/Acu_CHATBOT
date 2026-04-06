from django.urls import path
from . import views

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('session/<uuid:session_id>/', views.chat_view, name='chat_session'),
    path('api/chat/', views.chat_api, name='chat_api'),
    path('new-session/', views.new_session, name='new_session'),
]
