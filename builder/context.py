from .models import App


def sidebar(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    return {'sidebar_apps': App.objects.prefetch_related('tables')}
