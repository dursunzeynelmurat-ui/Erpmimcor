class BusinessRuleError(Exception):
    """İş kuralı ihlali. Mesaj kullanıcıya gösterilir; işlem geri alınır."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field
