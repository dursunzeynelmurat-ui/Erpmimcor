from .permissions import MATRIX, can


class _Perms:
    def __init__(self, user):
        self.user = user

    def __getitem__(self, action):
        return can(self.user, action.replace("_", "."))

    def __contains__(self, action):
        return action.replace("_", ".") in MATRIX


def permissions(request):
    """Şablonda {% if can.order_create %} biçiminde kullanılır."""
    return {"can": _Perms(getattr(request, "user", None))}
