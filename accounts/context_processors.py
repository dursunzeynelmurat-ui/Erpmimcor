from .catalog import BY_CODE
from .permissions import can


class _Perms:
    def __init__(self, user):
        self.user = user

    def __getitem__(self, action):
        code = action.replace("_", ".")
        return code in BY_CODE and can(self.user, code)

    def __contains__(self, action):
        return action.replace("_", ".") in BY_CODE


def permissions(request):
    """Şablonda {% if can.order_create %} biçiminde kullanılır. Yalnız görünürlük içindir; asıl kontrol sunucuda."""
    return {"can": _Perms(getattr(request, "user", None))}
