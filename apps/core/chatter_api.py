"""
Notes and follow-ups on a record over the API.

    /api/core/notes/?model=sales.salesorder&id=7          the record's notes
    POST {model, id, body}                               write one (core.add_note)
    DELETE /api/core/notes/{id}/                         remove one's own

    /api/core/follow-ups/?model=...&id=...               the record's follow-ups
    /api/core/follow-ups/?mine=true                      one's own, not done, by day
    POST {model, id, kind, summary, note, due_on, assigned_to, link}
    PATCH /api/core/follow-ups/{id}/                     reschedule or hand on
    POST /api/core/follow-ups/{id}/done/ {outcome}
    DELETE /api/core/follow-ups/{id}/                    one not done, its planner's or a manager's
    /api/core/follow-ups/people/                         who a follow-up may be for

All of it under the record's own reading rule (apps/core/endpoints.py):
a note or a follow-up on a record the login cannot open is not there.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .api import flag, record_or_404
from .chatter import FollowUp, FollowUpKind, Note
from .endpoints import may_read, model_named


def _record(request, data):
    """(model, pk) of the record a request names, once the login may read it."""
    model = model_named(str(data.get("model", "")))
    if model is None:
        raise ValidationError({"model": ["Name the record's kind as app.model."]})
    pk = str(data.get("id", ""))
    if not pk.isdigit():
        raise ValidationError({"id": ["Name the record by its id."]})
    if not request.user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"):
        raise PermissionDenied(f"Reading {model._meta.verbose_name_plural} is not yours.")
    if not may_read(request.user, model, int(pk)):
        raise NotFound()
    return model, int(pk)


def _readable(request, row):
    model = row.content_type.model_class()
    if model is None or not may_read(request.user, model, row.object_id):
        raise NotFound()
    return row


def _who(user):
    return (user.get_full_name() or user.get_username()) if user else ""


def _note(row, user):
    return {"id": row.id, "body": row.body, "by": _who(row.created_by), "at": row.created_at,
            "mine": row.created_by_id == user.pk}


def _follow_up(row, user, today, with_record=False):
    out = {
        "id": row.id, "kind": row.kind, "kind_label": row.get_kind_display(), "summary": row.summary,
        "note": row.note, "due_on": row.due_on, "assigned_to": row.assigned_to_id,
        "assigned_to_name": _who(row.assigned_to), "planned_by": _who(row.created_by),
        "done_on": row.done_on, "done_by": _who(row.done_by), "outcome": row.outcome, "link": row.link,
        # Worked out on reading, never stored: whether it is late depends on the day it is read.
        "state": "done" if row.done_on else "overdue" if row.due_on < today else "today" if row.due_on == today
        else "planned",
        "may_change": row.done_on is None and (user.pk in (row.created_by_id, row.assigned_to_id)
                                               or user.has_perm("core.change_followup")),
    }
    if with_record:
        model = row.content_type.model_class()
        record = model._default_manager.filter(pk=row.object_id).first() if model else None
        out["record"] = str(record) if record is not None else "(removed)"
        out["record_kind"] = str(model._meta.verbose_name).capitalize() if model else ""
    return out


class NoteViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        model, pk = _record(request, request.query_params)
        rows = Note.objects.filter(content_type=ContentType.objects.get_for_model(model), object_id=pk) \
            .select_related("created_by")[:500]
        return Response([_note(row, request.user) for row in rows])

    def create(self, request):
        if not request.user.has_perm("core.add_note"):
            raise PermissionDenied("Writing notes is not yours.")
        model, pk = _record(request, request.data)
        row = Note.objects.create(content_type=ContentType.objects.get_for_model(model), object_id=pk,
                                  body=str(request.data.get("body", "")), created_by=request.user,
                                  updated_by=request.user)
        return Response(_note(row, request.user), status=201)

    def destroy(self, request, pk=None):
        row = _readable(request, record_or_404(Note, pk, "id"))
        if row.created_by_id != request.user.pk:
            raise PermissionDenied("A note is removed only by whoever wrote it.")
        row.delete()
        return Response(status=204)


class FollowUpInput(serializers.Serializer):
    kind = serializers.ChoiceField(choices=FollowUpKind.choices, required=False)
    summary = serializers.CharField(max_length=255, required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    due_on = serializers.DateField(required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.all(), required=False)
    link = serializers.CharField(max_length=255, required=False, allow_blank=True)


class FollowUpViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        today = timezone.localdate()
        if flag(request.query_params, "mine", False):
            rows = FollowUp.objects.filter(assigned_to=request.user, done_on__isnull=True) \
                .select_related("content_type", "assigned_to", "created_by", "done_by")[:200]
            # Still one they may open: a customer handed to another rep takes its follow-ups out of reach.
            return Response([_follow_up(row, request.user, today, with_record=True) for row in rows
                             if row.content_type.model_class() is not None
                             and may_read(request.user, row.content_type.model_class(), row.object_id)])
        model, pk = _record(request, request.query_params)
        rows = FollowUp.objects.filter(content_type=ContentType.objects.get_for_model(model), object_id=pk) \
            .select_related("assigned_to", "created_by", "done_by")
        return Response([_follow_up(row, request.user, today) for row in rows])

    def create(self, request):
        if not request.user.has_perm("core.add_followup"):
            raise PermissionDenied("Planning follow-ups is not yours.")
        model, pk = _record(request, request.data)
        given = FollowUpInput(data=request.data)
        given.is_valid(raise_exception=True)
        data = given.validated_data
        missing = {key: ["Say this."] for key in ("summary", "due_on") if not data.get(key)}
        if missing:
            raise ValidationError(missing)
        with transaction.atomic():
            row = FollowUp.objects.create(
                content_type=ContentType.objects.get_for_model(model), object_id=pk,
                kind=data.get("kind", FollowUpKind.TODO), summary=data["summary"], note=data.get("note", ""),
                due_on=data["due_on"], assigned_to=data.get("assigned_to") or request.user, link=data.get("link", ""),
                created_by=request.user, updated_by=request.user)
        return Response(_follow_up(row, request.user, timezone.localdate()), status=201)

    def _changeable(self, request, pk):
        row = _readable(request, record_or_404(FollowUp, pk, "id"))
        if request.user.pk not in (row.created_by_id, row.assigned_to_id) \
                and not request.user.has_perm("core.change_followup"):
            raise PermissionDenied("Only whoever planned it, whoever it is for, or a manager changes it.")
        return row

    def partial_update(self, request, pk=None):
        row = self._changeable(request, pk)
        given = FollowUpInput(data=request.data, partial=True)
        given.is_valid(raise_exception=True)
        for key, value in given.validated_data.items():
            setattr(row, key, value)
        row.updated_by = request.user
        with transaction.atomic():
            row.save()
        return Response(_follow_up(row, request.user, timezone.localdate()))

    @action(detail=True, methods=["post"])
    def done(self, request, pk=None):
        row = self._changeable(request, pk)
        row.mark_done(request.user, outcome=str(request.data.get("outcome", "")))
        row.refresh_from_db()
        return Response(_follow_up(row, request.user, timezone.localdate()))

    def destroy(self, request, pk=None):
        row = _readable(request, record_or_404(FollowUp, pk, "id"))
        if row.created_by_id != request.user.pk and not request.user.has_perm("core.delete_followup"):
            raise PermissionDenied("Only whoever planned it, or a manager, removes it.")
        row.delete()
        return Response(status=204)

    @action(detail=False, methods=["get"])
    def people(self, request):
        """Who a follow-up may be put on: everyone who signs in. Asked again, for the record, when it is saved."""
        if not request.user.has_perm("core.add_followup"):
            raise PermissionDenied("Planning follow-ups is not yours.")
        users = get_user_model().objects.filter(is_active=True).order_by("first_name", "username")
        return Response([{"id": user.pk, "name": _who(user)} for user in users])

