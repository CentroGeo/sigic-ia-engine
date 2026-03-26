# Generated manually on 2026-03-17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('localidades', '0002_spatialization_progress'),
    ]

    operations = [
        migrations.AddField(
            model_name='spatialization',
            name='custom_instructions',
            field=models.TextField(blank=True, null=True),
        ),
    ]
