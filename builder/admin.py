from django.contrib import admin

from .models import App, Field, Table, View


class FieldInline(admin.TabularInline):
    model = Field
    extra = 0
    fields = ['key', 'name', 'type', 'required', 'unique', 'order']
    readonly_fields = ['key', 'type']


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ['name', 'key', 'order']


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ['name', 'app', 'key']
    list_filter = ['app']
    inlines = [FieldInline]


@admin.register(View)
class ViewAdmin(admin.ModelAdmin):
    list_display = ['name', 'table', 'key']
