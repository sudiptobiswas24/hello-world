"""
A list as someone left it, kept under a name: "Unpaid over 60 days",
"My customers in Gujarat". What is kept is the list's address and its
narrowing, nothing it showed: opened again, the list is asked afresh,
under the person's own permissions, so a kept view can show nobody
anything they could not have narrowed to themselves.

Each person's own. A view kept for the whole office would be a list
someone else's change could quietly redefine under the people using it.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

# Where in the list one was is not kept; how it was narrowed, searched,
# sorted and grouped is.
NOT_KEPT = frozenset({"page"})
MOST_KEYS = 20


class SavedFilter(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_filters")
    screen = models.CharField(max_length=255, help_text="The list's place in the application: /sales/invoices.")
    name = models.CharField(max_length=64)
    query = models.JSONField(default=dict, blank=True, help_text="The list's narrowing, as its address carries it.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["screen", "name"]
        constraints = [models.UniqueConstraint(fields=["user", "screen", "name"], name="saved_filter_name_once_a_list")]

    def __str__(self):
        return f"{self.name} ({self.screen})"

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": ["Give it a name."]})
        if not self.screen.startswith("/") or self.screen.startswith("//"):
            raise ValidationError({"screen": ["A place in the application, starting with /."]})
        if not isinstance(self.query, dict) or len(self.query) > MOST_KEYS or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in self.query.items()):
            raise ValidationError({"query": ["The list's narrowing: up to twenty names, each with its value as text."]})
        self.query = {key: value for key, value in self.query.items() if key not in NOT_KEPT}
        super().save(*args, **kwargs)


class SavedFilterSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedFilter
        fields = ["id", "screen", "name", "query", "created_at"]
        read_only_fields = ["created_at"]
        # The name is unique per list for each person; save() says so in words.
        validators = []


class SavedFilterViewSet(viewsets.ModelViewSet):
    """One's own kept views: ?screen=/sales/invoices for one list's."""

    permission_classes = [IsAuthenticated]
    serializer_class = SavedFilterSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        rows = SavedFilter.objects.filter(user=self.request.user)
        screen = self.request.query_params.get("screen")
        return rows.filter(screen=screen) if screen else rows

    def filter_queryset(self, queryset):
        return queryset  # narrowed by screen above; nothing else

    def perform_create(self, serializer):
        user = self.request.user
        data = serializer.validated_data
        if SavedFilter.objects.filter(user=user, screen=data["screen"], name=data["name"].strip()).exists():
            raise ValidationError({"name": [f"You have a view called {data['name'].strip()!r} on this list already."]})
        serializer.save(user=user)
