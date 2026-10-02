from django.urls import path

from . import views_ai, views_build, views_data

urlpatterns = [
    path('', views_data.home, name='home'),
    path('media/<path:path>', views_data.media, name='media'),

    # Building (staff)
    path('apps/new/', views_build.app_form, name='app_create'),
    path('apps/<slug:key>/edit/', views_build.app_form, name='app_edit'),
    path('apps/<slug:key>/delete/', views_build.app_delete, name='app_delete'),
    path('apps/<slug:key>/tables/new/', views_build.table_create, name='table_create'),
    path('t/<int:pk>/settings/', views_build.table_settings, name='table_settings'),
    path('t/<int:pk>/delete/', views_build.table_delete, name='table_delete'),
    path('t/<int:pk>/fields/new/', views_build.field_form, name='field_create'),
    path('fields/<int:field_id>/edit/', views_build.field_form, name='field_edit'),
    path('fields/<int:field_id>/delete/', views_build.field_delete, name='field_delete'),
    path('fields/<int:field_id>/move/', views_build.field_move, name='field_move'),
    path('t/<int:pk>/views/new/', views_build.view_form, name='view_create'),
    path('views/<int:view_id>/edit/', views_build.view_form, name='view_edit'),
    path('views/<int:view_id>/delete/', views_build.view_delete, name='view_delete'),

    # Data
    path('apps/<slug:key>/', views_data.app_detail, name='app_detail'),
    path('t/<int:pk>/', views_data.grid, name='table'),
    path('t/<int:pk>/v/<int:view_id>/', views_data.grid, name='table_view'),
    path('t/<int:pk>/new/', views_data.row_form, name='row_create'),
    path('r/<int:row_id>/', views_data.row_detail, name='row_detail'),
    path('r/<int:row_id>/edit/', views_data.row_form, name='row_edit'),
    path('r/<int:row_id>/delete/', views_data.row_delete, name='row_delete'),
    path('r/<int:row_id>/images/<int:field_id>/', views_data.images_upload, name='images_upload'),
    path('images/<int:image_id>/', views_data.image_action, name='image_action'),
    path('api/t/<int:pk>/search/', views_data.api_search, name='api_search'),
    path('api/r/<int:row_id>/cell/', views_data.api_cell, name='api_cell'),

    # AI and blueprints
    path('ai/', views_ai.prompt, name='ai_prompt'),
    path('ai/import/', views_ai.import_view, name='ai_import'),
    path('library/<str:name>/', views_ai.library_install, name='library_install'),
    path('export/', views_ai.export, name='export_all'),
    path('apps/<slug:key>/export/', views_ai.export, name='app_export'),
]
