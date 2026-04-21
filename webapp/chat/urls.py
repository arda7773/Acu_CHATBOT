from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.chat_view, name='chat'),
    path('session/<uuid:session_id>/', views.chat_view, name='chat_session'),
    path('api/chat/', views.chat_api, name='chat_api'),
    path('api/chat/stream/', views.chat_api_stream, name='chat_api_stream'),
    path('api/session/<uuid:session_id>/delete/', views.delete_session, name='delete_session'),
    path('new-session/', views.new_session, name='new_session'),
]
