"""
The books' own log, kept by the database (PostgreSQL): every INSERT, UPDATE and DELETE of a row of the books of
account, its old and new row as JSON, into core_ledgerchange, by trigger. The edit log the application writes
(apps/core/history.py) covers every path through the ORM; this one covers what goes around it as well: raw SQL,
psql, a restore script. Companies (Accounts) Rules 2014, rule 3(1) proviso: an audit trail of each transaction and
an edit log of each change, which cannot be disabled.

- The log is append-only: a trigger refuses its UPDATE and DELETE, another its TRUNCATE, and TRUNCATE is revoked
  from the role that owns it. Nothing prunes it (Companies Act s.128(5): eight years at least).
- Who acted: every write statement the application sends sets `erp.actor` for its transaction first
  (set_config(..., true), which is SET LOCAL), as "<user id>|<through>" (apps.core.history.acting). A change made
  with none set (psql, a script on its own connection) is recorded as through "unattributed"; the health check
  counts those, and names a books table that has lost its trigger.
- Two roles (migration web 0002, grant_app()): the owner, which migrations, backup and restore connect as, owns
  the tables, the triggers, the log and the trigger function, which writes the log as the owner (SECURITY
  DEFINER, fixed search_path). The application's role (settings.DATABASE_APP_ROLE) owns nothing: rows of the
  product tables to read and write, the sequences, the log to read only; no ALTER, TRUNCATE or TRIGGER, so it
  cannot disable a trigger or touch the log. Tables a later migration makes are granted by default privileges.
- Made by migration web 0001 (create(), idempotent; its reverse drop() removes the triggers and functions, and
  the log table only while it holds no row). SQLite has no such layer.

The table is not a Django-managed one, so a test's flush (TRUNCATE of every managed table) leaves it be.
"""

import json
import re
from decimal import Decimal

from django.db import models, transaction

# The books of account, by table: entries and their lines, invoices and bills (credit and debit notes are kinds
# of them), their lines and taxes, payments and what they settle, write-offs, stock movements, pay runs and slips,
# assets and depreciation, and the periods that close them.
BOOKS_TABLES = (
    "accounting_journalentry", "accounting_journalline", "accounting_accountingperiod", "accounting_payment",
    "sales_invoice", "sales_invoiceline", "sales_invoicelinetax", "sales_invoicepayment", "sales_invoicewriteoff",
    "purchasing_bill", "purchasing_billline", "purchasing_billlinetax", "purchasing_billpayment",
    "inventory_stockmovement",
    "hr_payrun", "hr_payslip", "hr_payslipline",
    "assets_fixedasset", "assets_depreciationentry",
)

LOG = "core_ledgerchange"
TRIGGER = "erp_ledger_change"
UNATTRIBUTED = "unattributed"


class _Exact(json.JSONDecoder):
    """A row's numbers as the database keeps them: a decimal stays a Decimal, never a float."""

    def __init__(self, *args, **kwargs):
        kwargs["parse_float"] = Decimal
        super().__init__(*args, **kwargs)


class LedgerChange(models.Model):
    """One row of the books written, as the database saw it. Read only: the table refuses anything else."""

    at = models.DateTimeField()
    table_name = models.CharField(max_length=63)
    operation = models.CharField(max_length=8)
    row_id = models.TextField(null=True)
    old_row = models.JSONField(null=True, decoder=_Exact)
    new_row = models.JSONField(null=True, decoder=_Exact)
    acting_user_id = models.BigIntegerField(null=True)
    through = models.CharField(max_length=64)
    transaction_id = models.BigIntegerField()
    db_user = models.CharField(max_length=63)

    not_edit_logged = "the database's own log of the books"
    numbering_exempt = "appended by trigger, never locked"

    class Meta:
        managed = False
        db_table = LOG
        ordering = ["-id"]

    def __str__(self):
        return f"{self.operation} {self.table_name} {self.row_id}"


CREATE_LOG = f"""
CREATE TABLE IF NOT EXISTS {LOG} (
    id bigserial PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT clock_timestamp(),
    table_name varchar(63) NOT NULL,
    operation varchar(8) NOT NULL,
    row_id text,
    old_row jsonb,
    new_row jsonb,
    acting_user_id bigint,
    through varchar(64) NOT NULL,
    transaction_id bigint NOT NULL DEFAULT txid_current(),
    db_user varchar(63) NOT NULL DEFAULT current_user
);
CREATE INDEX IF NOT EXISTS {LOG}_at ON {LOG} (at);
CREATE INDEX IF NOT EXISTS {LOG}_row ON {LOG} (table_name, row_id);

CREATE OR REPLACE FUNCTION {TRIGGER}() RETURNS trigger LANGUAGE plpgsql
    SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    actor text := coalesce(current_setting('erp.actor', true), '');
    who text := split_part(actor, '|', 1);
BEGIN
    INSERT INTO {LOG} (table_name, operation, row_id, old_row, new_row, acting_user_id, through)
    VALUES (TG_TABLE_NAME, TG_OP,
            CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ->> 'id' ELSE to_jsonb(NEW) ->> 'id' END,
            CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
            CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
            CASE WHEN who ~ '^[0-9]+$' THEN who::bigint END,
            coalesce(nullif(substr(split_part(actor, '|', 2), 1, 64), ''), '{UNATTRIBUTED}'));
    RETURN NULL;
END
$$;

CREATE OR REPLACE FUNCTION {TRIGGER}_kept() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is kept as written: % of it is refused (Companies (Accounts) Rules 2014, rule 3(1)).', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$;

DROP TRIGGER IF EXISTS {LOG}_kept ON {LOG};
CREATE TRIGGER {LOG}_kept BEFORE UPDATE OR DELETE ON {LOG} FOR EACH ROW EXECUTE FUNCTION {TRIGGER}_kept();
DROP TRIGGER IF EXISTS {LOG}_not_truncated ON {LOG};
CREATE TRIGGER {LOG}_not_truncated BEFORE TRUNCATE ON {LOG} FOR EACH STATEMENT EXECUTE FUNCTION {TRIGGER}_kept();
-- The application's own edit log (apps/core/history.py) the same: added to, never changed or removed. Not its
-- TRUNCATE: a test's flush empties it (the application's role has no TRUNCATE on it anyway).
DROP TRIGGER IF EXISTS core_recordevent_kept ON core_recordevent;
CREATE TRIGGER core_recordevent_kept BEFORE UPDATE OR DELETE ON core_recordevent FOR EACH ROW
    EXECUTE FUNCTION {TRIGGER}_kept();
REVOKE TRUNCATE ON {LOG} FROM PUBLIC;
REVOKE TRUNCATE ON {LOG} FROM CURRENT_USER;
"""


def _on(table):
    return (f"DROP TRIGGER IF EXISTS {TRIGGER} ON {table};\n"
            f"CREATE TRIGGER {TRIGGER} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {TRIGGER}();\n")


def create(connection, tables=BOOKS_TABLES):
    """The log, its guards and a trigger on each of `tables`; run again, it changes nothing. PostgreSQL only."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(CREATE_LOG)
        for table in tables:
            cursor.execute(_on(table))


def drop(connection, tables=BOOKS_TABLES):
    """What create() made: the triggers and functions; the log itself only while it is empty, never its rows."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {table}")
        cursor.execute(f"DROP FUNCTION IF EXISTS {TRIGGER}() CASCADE")
        cursor.execute("DROP TRIGGER IF EXISTS core_recordevent_kept ON core_recordevent")
        cursor.execute(f"SELECT to_regclass('{LOG}') IS NOT NULL")
        if cursor.fetchone()[0]:
            cursor.execute(f"SELECT EXISTS (SELECT 1 FROM {LOG})")
            if not cursor.fetchone()[0]:
                cursor.execute(f"DROP TABLE {LOG}")
                cursor.execute(f"DROP FUNCTION IF EXISTS {TRIGGER}_kept() CASCADE")


def grant_app(connection, role, password=""):
    """
    The application's role made (if absent, with `password`) and given rows to read and write and nothing more:
    no table of its own, no TRUNCATE or TRIGGER, the log only to read. Run as the owner, at every migrate
    (apps.core.apps), so a restored or rebuilt database is put right again; idempotent.
    """
    if connection.vendor != "postgresql" or not role:
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", [role])
        owner, exists = cursor.fetchone()
        if owner == role:
            raise RuntimeError(f"The application's role {role} is the role migrating: it would own the tables. "
                               f"Migrate as DATABASE_OWNER_URL's role.")
        if not exists:
            if not password:
                raise RuntimeError(f"No database role {role}: create it, or give its password in DATABASE_URL.")
            cursor.execute("SELECT format('CREATE ROLE %%I LOGIN PASSWORD %%L', %s::text, %s::text)", [role, password])
            cursor.execute(cursor.fetchone()[0])
        app = quote(role)
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                       "WHERE r.rolname = %s AND c.relnamespace = 'public'::regnamespace)", [role])
        if cursor.fetchone()[0]:
            raise RuntimeError(f"The application's role {role} owns tables: give them to {owner} (REASSIGN OWNED).")
        for statement in (
            f"GRANT CONNECT ON DATABASE {quote(connection.settings_dict['NAME'])} TO {app}",
            f"GRANT USAGE ON SCHEMA public TO {app}",
            f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {app}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app}",
        ):
            cursor.execute(statement)
        cursor.execute(f"SELECT to_regclass('{LOG}') IS NOT NULL")
        if cursor.fetchone()[0]:
            cursor.execute(f"REVOKE ALL ON {LOG} FROM {app}")
            cursor.execute(f"GRANT SELECT ON {LOG} TO {app}")
        # The application's edit log: read and added to, never changed or removed.
        cursor.execute(f"REVOKE ALL ON core_recordevent FROM {app}")
        cursor.execute(f"GRANT SELECT, INSERT ON core_recordevent TO {app}")


def grant_after_migrate(sender, using="default", **kwargs):
    """
    At the end of every migrate, as the owner: the log, its guards and the triggers made again (idempotent, so a
    guard added since, or a table a migration added, has its own), and the application's role given what it may
    do, and no more.
    """
    from django.conf import settings
    from django.db import connections

    create(connections[using])
    grant_app(connections[using], getattr(settings, "DATABASE_APP_ROLE", ""),
              getattr(settings, "DATABASE_APP_PASSWORD", ""))


def present(connection):
    """Whether this database keeps the log at all (PostgreSQL with the migration run)."""
    if connection.vendor != "postgresql":
        return False
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT to_regclass('{LOG}') IS NOT NULL")
        return cursor.fetchone()[0]


def tables_without_their_trigger(connection, tables=BOOKS_TABLES):
    """
    The books tables whose trigger is gone or does not fire: disabled (D), or set to fire only in replica mode (R),
    which an ordinary session never is. On only when it fires always (A) or at origin (O, the default).
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            WHERE t.tgname = %s AND t.tgenabled IN ('O', 'A') AND c.relname = ANY(%s)""", [TRIGGER, list(tables)])
        kept = {row[0] for row in cursor.fetchall()}
    return [table for table in tables if table not in kept]


# -- who is acting, told to the database --------------------------------------------------------------------

WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|WITH|MERGE)\b", re.I)


def _actor():
    from .history import acting

    user, through = acting()
    return f"{user.pk if user is not None else ''}|{through}"


def _stamped(connection, actor):
    mark = connection.__dict__.get("_erp_actor_mark")
    return mark is not None and mark.actor == actor and any(entry[1] is mark for entry in connection.run_on_commit)


def _tell(connection, cursor, actor):
    cursor.cursor.execute("SELECT set_config('erp.actor', %s, true)", [actor])

    def mark():
        pass

    mark.actor = actor
    # Kept while the setting is: run at commit and dropped at a rollback (a savepoint's too, which PostgreSQL
    # also undoes the setting at), so the next transaction is told again.
    transaction.on_commit(mark, using=connection.alias)
    connection.__dict__["_erp_actor_mark"] = mark


def stamp_actor(execute, sql, params, many, context):
    """Before a write, its transaction told who is acting (once a transaction, again when that changes)."""
    if not WRITE.match(sql or ""):
        return execute(sql, params, many, context)
    connection, cursor = context["connection"], context["cursor"]
    actor = _actor()
    if connection.in_atomic_block:
        if not _stamped(connection, actor):
            _tell(connection, cursor, actor)
        return execute(sql, params, many, context)
    # Autocommit: the statement is its own transaction, so the setting goes in one with it.
    with transaction.atomic(using=connection.alias):
        _tell(connection, cursor, actor)
        return execute(sql, params, many, context)


def watch(sender=None, connection=None, **kwargs):
    if connection is not None and connection.vendor == "postgresql" and stamp_actor not in connection.execute_wrappers:
        connection.execute_wrappers.insert(0, stamp_actor)


def install():
    """Every PostgreSQL connection tells its writes who is acting (apps.core.apps.ready)."""
    from django.db import connections
    from django.db.backends.signals import connection_created

    connection_created.connect(watch, weak=False, dispatch_uid="apps.core.ledger_log.watch")
    for open_connection in connections.all(initialized_only=True):
        watch(connection=open_connection)
