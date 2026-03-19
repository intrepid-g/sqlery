"""Add DaemonCommand model for daemon command queue."""

from django.db import migrations, models
import django.core.serializers.json


class Migration(migrations.Migration):

    dependencies = [
        ("sqlery", "0024_add_timestamp_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="DaemonCommand",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "command",
                    models.CharField(
                        choices=[
                            ("manual_intervention", "Manual Intervention"),
                            ("restart_workers", "Restart Workers"),
                            ("cleanup_now", "Cleanup Now"),
                            ("enforce_deadlines", "Enforce Deadlines"),
                        ],
                        max_length=100,
                    ),
                ),
                (
                    "payload",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "result",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        encoder=django.core.serializers.json.DjangoJSONEncoder,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "processed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
            ],
            options={
                "db_table": "sqlery_daemon_command",
                "ordering": ["created_at"],
                "verbose_name": "Daemon Command",
                "verbose_name_plural": "Daemon Commands",
                "indexes": [
                    models.Index(
                        fields=["status", "created_at"],
                        name="sqlery_daem_status_created_idx",
                    ),
                ],
            },
        ),
    ]
