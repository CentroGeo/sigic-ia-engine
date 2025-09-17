from django.urls import path
from . import views

urlpatterns = [
    path('v1', views.chat, name='chat-api'),
    path('history/generate', views.historyGenerate, name='history-generate'),
    path('history/user', views.historyUser, name='history-user'),
    path('history/getchats', views.get_chat_histories, name='history-chats'),
    
    path('history/title', views.historyTitle, name='history-title'),
    path('history/remove', views.hisotryRemove, name='history-remove'),
]