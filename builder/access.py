from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def staff_required(view):
    """Building (apps, tables, fields, views, blueprints) is for staff users."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied('Only staff users can change how apps are built.')
        return view(request, *args, **kwargs)
    return wrapper
